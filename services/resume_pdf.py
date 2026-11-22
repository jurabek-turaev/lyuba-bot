"""
Resume PDF built from the candidate's form answers (RenderCV, classic theme, A4).

The PDF is universal: it holds no position, expected salary or start date,
so the candidate can use it anywhere. Those go only on the admin's card.

RenderCV runs in a separate virtualenv (see resume_render_worker.py), started as
a subprocess for each resume.

Form answers (the dict built by the candidate's form; missing keys are skipped):
    full_name, phone ("+998..."), city, birth_date ("DD.MM.YYYY"), gender,
    marital_status, education_level,
    education: [{institution, specialty, year}],
    jobs: [{company, position, start ("YYYY-MM"), end ("YYYY-MM" or "present")}],
    languages: [{name, level}], computer_skills, driver_license, about
"""
import asyncio
import json
import logging
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path

import config

logger = logging.getLogger(__name__)

WORKER = Path(__file__).with_name("resume_render_worker.py")
RENDER_TIMEOUT_SECONDS = 60
MAX_PARALLEL_RENDERS = 2  # each render is a separate Python process (~150 MB)
_render_slots = asyncio.Semaphore(MAX_PARALLEL_RENDERS)

UZ_MONTHS = [
    "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
    "Iyul", "Avgust", "Sentyabr", "Oktyabr", "Noyabr", "Dekabr",
]

LOCALE = {
    "language": "english",  # the base locale; every visible word is overridden below
    "present": "hozirgacha",
    "last_updated": "Yangilangan:",
    "month": "oy", "months": "oy", "year": "yil", "years": "yil",
    "month_names": UZ_MONTHS,
    # full names: abbreviations like "Iyu" would be ambiguous (Iyun / Iyul)
    "month_abbreviations": UZ_MONTHS,
    "phrases": {"degree_with_area": "DEGREE, AREA"},
}

DESIGN = {
    "theme": "classic",
    "page": {"size": "a4", "show_top_note": False},
    "header": {"connections": {"phone_number_format": "international"}},  # +998 ...
    # no separate degree column: it leaves an empty indent before every school
    "templates": {"education_entry": {"degree_column": None}},
}

# Characters RenderCV's markdown (Python-Markdown) treats specially and lets escape
# with a backslash; the candidate's text must stay literal
_MARKDOWN_SPECIAL = "\\`*_{}[]()>#+-.!"


class ResumeRenderError(Exception):
    """The PDF could not be made (renderer missing, failed or too slow)."""


def photo_extension(photo: bytes) -> str | None:
    """File extension for the photo bytes (Typst reads the format from it), None if unknown."""
    if photo.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if photo.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if photo[:4] == b"RIFF" and photo[8:12] == b"WEBP":
        return "webp"
    return None


def md(text) -> str:
    """The candidate's text, escaped so markdown never changes it."""
    return "".join("\\" + ch if ch in _MARKDOWN_SPECIAL else ch for ch in str(text).strip())


def age_on(birth_date: str, today: date) -> int | None:
    try:
        born = datetime.strptime(birth_date, "%d.%m.%Y").date()
    except (TypeError, ValueError):
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def build_cv(form: dict, photo_file: str | None = None, today: date | None = None) -> dict:
    """The RenderCV input for the form (pure function: no files, no processes)."""
    today = today or date.today()
    sections = {}

    personal = []
    if form.get("birth_date"):
        age = age_on(form["birth_date"], today)
        line = f"**Tugʻilgan sana:** {md(form['birth_date'])}"
        personal.append(line + (f" ({age} yosh)" if age is not None else ""))
    pairs = [("Jinsi", form.get("gender")), ("Oilaviy holati", form.get("marital_status"))]
    pairs_text = " · ".join(f"**{label}:** {md(value)}" for label, value in pairs if value)
    if pairs_text:
        personal.append(pairs_text)
    if form.get("education_level"):
        personal.append(f"**Maʼlumoti:** {md(form['education_level'])}")
    if personal:
        sections["Shaxsiy maʼlumotlar"] = personal

    education = []
    for item in form.get("education") or []:
        entry = {
            "institution": md(item["institution"]),
            "area": md(item.get("specialty") or form.get("education_level") or "—"),
        }
        if item.get("year"):
            entry["date"] = int(item["year"])  # an int is shown as the year alone
        education.append(entry)
    if education:
        sections["Maʼlumoti"] = education

    jobs = []
    for item in form.get("jobs") or []:
        entry = {"company": md(item["company"]), "position": md(item["position"])}
        if item.get("start"):
            entry["start_date"] = item["start"]
            entry["end_date"] = item.get("end") or "present"
        jobs.append(entry)
    if jobs:
        sections["Ish tajribasi"] = jobs

    skills = []
    languages = [
        f"{md(lang['name'])} ({md(lang['level'])})" if lang.get("level") else md(lang["name"])
        for lang in form.get("languages") or []
    ]
    if languages:
        skills.append({"label": "Tillar", "details": ", ".join(languages)})
    if form.get("computer_skills"):
        skills.append({"label": "Kompyuter", "details": md(form["computer_skills"])})
    if form.get("driver_license"):
        skills.append({"label": "Haydovchilik guvohnomasi", "details": md(form["driver_license"])})
    if skills:
        sections["Koʻnikmalar"] = skills

    paragraphs = [md(line) for line in str(form.get("about") or "").splitlines() if line.strip()]
    if paragraphs:
        sections["Oʻzim haqimda"] = paragraphs

    cv = {"name": form["full_name"], "sections": sections}
    data = {"cv": cv, "design": DESIGN, "locale": LOCALE}
    if form.get("phone"):
        cv["phone"] = form["phone"]
        # RenderCV refuses the whole resume for a number it cannot parse; the worker
        # then moves the number into this section as plain text instead
        data["phone_fallback"] = {
            "section": "Shaxsiy maʼlumotlar",
            "line": f"**Telefon:** {md(form['phone'])}",
        }
    if form.get("city"):
        cv["location"] = form["city"]
    if photo_file:
        cv["photo"] = photo_file

    return data


async def render_resume_pdf(form: dict, photo: bytes | None = None) -> bytes:
    """The resume PDF for the form answers. Raises ResumeRenderError on failure."""
    python = config.RESUME_RENDERER_PYTHON
    if not python or not Path(python).exists():
        raise ResumeRenderError(f"RenderCV python not found: {python!r} (see README)")

    async with _render_slots:
        work_dir = Path(tempfile.mkdtemp(prefix="resume-"))
        try:
            photo_file = None
            extension = photo_extension(photo) if photo else None
            if extension:
                photo_file = f"photo.{extension}"
                (work_dir / photo_file).write_bytes(photo)
            elif photo:
                logger.warning("Unknown photo format, the resume is made without it")
            cv = build_cv(form, photo_file)
            (work_dir / "cv.json").write_text(json.dumps(cv, ensure_ascii=False), encoding="utf-8")

            process = await asyncio.create_subprocess_exec(
                python, str(WORKER), str(work_dir),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), RENDER_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise ResumeRenderError("RenderCV took too long")

            if process.returncode != 0:
                error = stderr.decode("utf-8", "replace").strip()
                logger.error("RenderCV failed:\n%s", error)
                raise ResumeRenderError(error.splitlines()[-1] if error else "RenderCV failed")
            return (work_dir / "resume.pdf").read_bytes()
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
