"""
Renders one resume PDF with RenderCV.

Runs in its OWN virtualenv (requirements-rendercv.txt), never inside the bot:
RenderCV needs a newer pydantic than aiogram allows. So this file must not
import anything from the bot.

Usage:  python resume_render_worker.py WORK_DIR
Reads WORK_DIR/cv.json (a RenderCV input; a photo path in it is relative to
WORK_DIR) and writes WORK_DIR/resume.pdf. Any error -> traceback on stderr, exit 1.

Optional key "phone_fallback": {"section", "line"}: when cv.phone is not a valid
number, it is removed from the header and "line" is put first in that section.
"""
import json
import pathlib
import sys


def _apply_phone_fallback(data: dict) -> None:
    fallback = data.pop("phone_fallback", None)
    phone = data["cv"].get("phone")
    if not fallback or not phone:
        return
    import phonenumbers

    try:
        valid = phonenumbers.is_valid_number(phonenumbers.parse(phone, None))
    except phonenumbers.NumberParseException:
        valid = False
    if not valid:
        del data["cv"]["phone"]
        sections = data["cv"].setdefault("sections", {})
        sections[fallback["section"]] = [fallback["line"], *sections.get(fallback["section"], [])]


def main() -> None:
    work_dir = pathlib.Path(sys.argv[1]).resolve()
    data = json.loads((work_dir / "cv.json").read_text(encoding="utf-8"))
    _apply_phone_fallback(data)
    data["settings"] = {
        **data.get("settings", {}),
        "render_command": {
            "output_folder": str(work_dir / "out"),
            "dont_generate_html": True,
            "dont_generate_markdown": True,
            "dont_generate_png": True,
        },
    }

    # Pinned version (requirements-rendercv.txt): these are RenderCV's internal modules
    from rendercv.renderer.pdf_png import generate_pdf
    from rendercv.renderer.typst import generate_typst
    from rendercv.schema.rendercv_model_builder import build_rendercv_model_from_commented_map

    model = build_rendercv_model_from_commented_map(data, input_file_path=work_dir / "cv.yaml")
    pdf_path = generate_pdf(model, generate_typst(model))
    pathlib.Path(pdf_path).replace(work_dir / "resume.pdf")


if __name__ == "__main__":
    main()
