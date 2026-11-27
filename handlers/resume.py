"""
Candidate side of the hiring flow.

/start (anybody who is not an employee):
    consent -> "📝 Rezyume yaratish": the form, the bot makes a PDF resume
            or "📎 Tayyor rezyume yuborish": own file + name, phone, position, salary
    -> preview (send / edit a section / cancel) -> the admin gets a card

Where the candidate is in the form lives in the DATABASE (application_drafts:
step + answers), not in FSM memory: a restart never loses a half-filled form,
and /start offers to continue it. Every button carries its step key, so a
button of an older question is recognised as stale.

This router must be included BEFORE handlers/application.py, whose catch-all
handlers for unapproved users must stay last.
"""
import hashlib
import inspect
import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Callable

from aiogram import Bot, F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

import database as db
from services import hr_format
from services.resume_pdf import ResumeRenderError, render_resume_pdf
from .application import (
    PENDING_TEXT,
    Unapproved,
    format_duration,
    notify_admin,
    refresh_card,
    remaining_cooldown,
    send_application_card,
)

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PHOTO_BYTES = 10 * 1024 * 1024
UPLOAD_EXTENSIONS = (".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png")
MIN_AGE, MAX_AGE = 16, 70
MIN_SALARY, MAX_SALARY = 100_000, 1_000_000_000

BACK_TEXT = "⬅️ Orqaga"
YES, NO = "y", "n"

GENDERS = ["Erkak", "Ayol"]
MARITAL_STATUSES = ["Oilali", "Oila qurmagan"]
START_OPTIONS = ["Darhol", "1 haftadan keyin", "2 haftadan keyin", "1 oydan keyin"]
EDUCATION_LEVELS = ["Oliy", "Tugallanmagan oliy", "Oʻrta maxsus", "Oʻrta"]
LANGUAGES = ["Oʻzbek", "Rus", "Ingliz", "Tojik", "Turk"]
LANGUAGE_LEVELS = ["Ona tili", "Erkin", "Oʻrta", "Boshlangʻich"]
LICENSES = ["Yoʻq", "B", "C", "B, C", "B, C, D"]


class InputError(Exception):
    """A wrong answer: the message is shown to the candidate, the question stays."""


# ===========================
# Parsing typed answers
# ===========================
_UZ_LETTER_APOSTROPHE = re.compile(r"([oOgG])['`‘’ʼ]")
_OTHER_APOSTROPHE = re.compile(r"['`‘’]")


def clean_text(text: str) -> str:
    """Single spaces, Uzbek letters oʻ/gʻ and the tutuq belgisi ʼ (the PDF looks right)."""
    text = " ".join(text.split())
    text = _UZ_LETTER_APOSTROPHE.sub(lambda m: m.group(1) + "ʻ", text)
    return _OTHER_APOSTROPHE.sub("ʼ", text)


def text_parser(min_len: int, max_len: int, what: str) -> Callable[[str, dict], str]:
    def parse(text: str, data: dict) -> str:
        value = clean_text(text)
        if len(value) < min_len:
            raise InputError(f"{what} juda qisqa.")
        if len(value) > max_len:
            raise InputError(f"{what} juda uzun (ko'pi bilan {max_len} ta belgi).")
        return value
    return parse


def parse_full_name(text: str, data: dict) -> str:
    name = clean_text(text)
    if not 5 <= len(name) <= 100:
        raise InputError("Familiya va ismingizni to'liq yozing.")
    if any(not (ch.isalpha() or ch in " -.ʻʼ") for ch in name) or len(name.split()) < 2:
        raise InputError("Faqat harflardan iborat familiya va ismni yozing. Masalan: Valiyev Ali")
    return name


def parse_birth_date(text: str, data: dict, today: date | None = None) -> str:
    match = re.fullmatch(r"(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})", text.strip())
    if not match:
        raise InputError("Sanani kun.oy.yil ko'rinishida yozing. Masalan: 15.03.1998")
    day, month, year = map(int, match.groups())
    try:
        born = date(year, month, day)
    except ValueError:
        raise InputError("Bunday sana yo'q. Qaytadan tekshirib yozing.")
    value = born.strftime("%d.%m.%Y")
    age = hr_format.age(value, today)
    if age is None or not MIN_AGE <= age <= MAX_AGE:
        raise InputError(f"Yoshingiz {MIN_AGE} dan {MAX_AGE} gacha bo'lishi kerak. Sanani tekshiring.")
    return value


def parse_salary(text: str, data: dict) -> int:
    value = text.lower().replace(" ", " ")
    value = re.sub(r"(so['`‘’ʻʼ]?m|sum|сум|сўм|uzs)", "", value)
    millions = re.search(r"(mln|million|млн)", value) is not None
    value = re.sub(r"(mln|million|млн)", "", value).replace(" ", "")
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", value):
        value = re.sub(r"[.,]", "", value)  # 5.000.000 / 5,000,000
    if not re.fullmatch(r"\d+([.,]\d+)?", value):
        raise InputError("Maoshni so'mda raqam bilan yozing. Masalan: 5 000 000")
    amount = float(value.replace(",", "."))
    amount = round(amount * 1_000_000) if millions else round(amount)
    if not MIN_SALARY <= amount <= MAX_SALARY:
        raise InputError("Maoshni to'liq so'mda yozing. Masalan: 5 000 000")
    return amount


def parse_year(text: str, data: dict) -> int:
    if not re.fullmatch(r"\d{4}", text.strip()):
        raise InputError("Yilni 4 ta raqam bilan yozing. Masalan: 2019")
    year = int(text)
    if not 1950 <= year <= date.today().year + 7:
        raise InputError("Yilni tekshirib qaytadan yozing.")
    return year


def parse_month_year(text: str, data: dict) -> str:
    match = re.fullmatch(r"(\d{1,2})[./\-](\d{4})", text.strip())
    if not match:
        raise InputError("Oy va yilni oy.yil ko'rinishida yozing. Masalan: 03.2021")
    month, year = map(int, match.groups())
    today = date.today()
    if not 1 <= month <= 12 or year < 1950 or (year, month) > (today.year, today.month):
        raise InputError("Bunday oy bo'lishi mumkin emas. Tekshirib qaytadan yozing.")
    return f"{year:04d}-{month:02d}"


def parse_job_end(text: str, data: dict) -> str:
    end = parse_month_year(text, data)
    if end < data.get("_item", {}).get("start", ""):
        raise InputError("Ishdan ketgan sana boshlagan sanadan oldin bo'lishi mumkin emas.")
    return end


# ===========================
# Steps
# ===========================
@dataclass(frozen=True)
class Step:
    key: str
    prompt: Callable[[dict], str]
    input: str = "text"      # text | choice | contact | photo | file | preview
    options: Callable | None = None  # data -> [(code, label)] (may be async)
    parse: Callable | None = None    # typed text -> value (raises InputError)
    apply: Callable | None = None    # (data, value) -> next step key or None (may be async)
    skip: bool = False
    skip_to: str | None = None  # where "skip" leads (default: the next step)
    counts: bool = True         # numbered in the progress
    section: str | None = None
    loop: str | None = None     # "add another?" step of this list


def _labels(values):
    return lambda data: [(str(i), label) for i, label in enumerate(values)]


def _yes_no(yes: str, no: str):
    return lambda data: [(YES, yes), (NO, no)]


def _set(field):
    def apply(data, value):
        data[field] = value
    return apply


def _set_item(field):
    def apply(data, value):
        data.setdefault("_item", {})[field] = value
    return apply


def _more(loop: str, again: str):
    """'Add another?': the current item joins the list; "yes" starts the next one."""
    def apply(data, value):
        data.setdefault(loop, []).append(data.pop("_item", {}))
        data["_item"] = {}
        return again if value == YES else None
    return apply


async def _department_options(data):
    return [(str(dept_id), name) for dept_id, name, *_ in await db.list_departments(active_only=True)]


async def _position_options(data):
    if data.get("department_id") is None:
        return []
    return [(str(pos_id), name) for pos_id, name, _ in await db.list_positions(data["department_id"])]


async def _apply_department(data, value):
    """
    A department without positions works as before positions existed: the
    department's name stands for the position and the position question is skipped.
    """
    data["department_id"], data["department"] = value
    data.pop("position_id", None)
    data["position"] = None
    if not await db.list_positions(data["department_id"]):
        data["position"] = data["department"]
        return following(data, "position")
    return None


def _apply_position(data, value):
    data["position_id"], data["position"] = value


def _apply_experience(data, value):
    data["jobs"] = []
    data["_item"] = {}
    return None if value == YES else "lang_name"


def _apply_license(data, value):
    data["driver_license"] = None if value == LICENSES[0] else value


def _apply_job_end(data, value):
    data.setdefault("_item", {})["end"] = value


def _item_title(data, *fields):
    item = data.get("_item") or {}
    return ", ".join(str(item[f]) for f in fields if item.get(f))


def _p(text):
    return lambda data: text


STEPS_LIST = [
    Step("consent", _p(
        "Assalomu alaykum! <b>Lyuba</b> kompaniyasining ishga qabul qilish botiga xush kelibsiz.\n\n"
        "Ariza yuborish uchun shaxsiy ma'lumotlaringiz kerak bo'ladi: ism, telefon, tug'ilgan sana, "
        "rasm, ma'lumotingiz va ish tajribangiz. Ular faqat ishga qabul qilish uchun ishlatiladi "
        "va boshqalarga berilmaydi. Ishga qabul qilinmagan nomzodlarning ma'lumotlari 1 yildan "
        "keyin o'chiriladi.\n\nShunga rozimisiz?"),
        input="choice", options=_yes_no("✅ Roziman", "❌ Rozi emasman"), counts=False),
    Step("choose", _p(
        "Arizani qanday yuborasiz?\n\n"
        "📝 <b>Rezyume yaratish</b> — savollarga javob berasiz, bot siz uchun PDF rezyume "
        "tayyorlaydi (5–10 daqiqa).\n"
        "📎 <b>Tayyor rezyume</b> — o'zingizdagi rezyume faylini yuborasiz (PDF, Word yoki rasm)."),
        input="choice", options=lambda data: [("form", "📝 Rezyume yaratish"), ("upload", "📎 Tayyor rezyume")],
        counts=False),

    Step("up_file", _p("📎 Rezyume faylingizni yuboring: PDF, Word (DOC, DOCX) yoki rasm. Hajmi 20 MB gacha."),
         input="file", section="file"),

    Step("full_name", _p("Familiya, ism va otangizning ismini yozing.\nMasalan: <i>Valiyev Ali Karimovich</i>"),
         parse=parse_full_name, apply=_set("full_name"), section="personal"),
    Step("birth_date", _p("Tug'ilgan sanangizni kun.oy.yil ko'rinishida yozing.\nMasalan: <i>15.03.1998</i>"),
         parse=parse_birth_date, apply=_set("birth_date"), section="personal"),
    Step("gender", _p("Jinsingiz:"), input="choice", options=_labels(GENDERS), apply=_set("gender"),
         section="personal"),
    Step("phone", _p("📱 Telefon raqamingizni pastdagi tugma orqali yuboring."),
         input="contact", section="personal"),
    Step("city", _p("Qayerda yashaysiz? Shahar yoki tumanni yozing.\nMasalan: <i>Qarshi shahri</i>"),
         parse=text_parser(2, 100, "Manzil"), apply=_set("city"), section="personal"),
    Step("marital_status", _p("Oilaviy holatingiz:"), input="choice", options=_labels(MARITAL_STATUSES),
         apply=_set("marital_status"), section="personal"),
    Step("photo", _p("📷 Rezyume uchun rasmingizni yuboring (3×4, yuzingiz aniq ko'rinsin)."),
         input="photo", skip=True, section="personal"),

    Step("department", _p("🏢 Qaysi bo'limga ariza topshiryapsiz?"), input="choice",
         options=_department_options, apply=_apply_department, section="position"),
    Step("position", lambda data: f"🏢 Bo'lim: <b>{escape(data.get('department') or '—')}</b>\n\n"
                                  "💼 Qaysi lavozimga ariza topshiryapsiz?",
         input="choice", options=_position_options, apply=_apply_position, counts=False, section="position"),
    Step("expected_salary", _p(
        "💰 Qancha oylik maosh kutyapsiz? So'mda yozing.\nMasalan: <i>5 000 000</i>\n\n"
        "<i>Bu faqat adminga ko'rinadi, rezyumega yozilmaydi.</i>"),
        parse=parse_salary, apply=_set("expected_salary"), section="position"),
    Step("start_availability", _p("🗓 Qachondan ishga chiqa olasiz?"), input="choice",
         options=_labels(START_OPTIONS), apply=_set("start_availability"), section="position"),

    Step("education_level", _p("🎓 Ma'lumotingiz:"), input="choice", options=_labels(EDUCATION_LEVELS),
         apply=_set("education_level"), section="education"),
    Step("edu_institution", lambda data: (
        "Yana bir o'quv yurti nomini yozing:" if data.get("education") else
        "Qayerda o'qigansiz? O'quv yurti nomini yozing.\nMasalan: <i>Qarshi davlat universiteti</i>"),
        parse=text_parser(2, 150, "Nomi"), apply=_set_item("institution"),
        skip=True, skip_to="has_experience", section="education"),
    Step("edu_specialty", _p("Mutaxassisligingiz (yo'nalishingiz)?\nMasalan: <i>Iqtisodiyot</i>"),
         parse=text_parser(2, 100, "Yo'nalish"), apply=_set_item("specialty"),
         skip=True, counts=False, section="education"),
    Step("edu_year", _p("Bitirgan (yoki bitiradigan) yilingiz?\nMasalan: <i>2019</i>"),
         parse=parse_year, apply=_set_item("year"), skip=True, counts=False, section="education"),
    Step("edu_more", lambda data: f"✅ Qo'shildi: <b>{escape(_item_title(data, 'institution', 'specialty'))}</b>"
                                  "\n\nYana o'quv yurti qo'shasizmi?",
         input="choice", options=_yes_no("➕ Ha, qo'shish", "➡️ Yo'q, davom etish"),
         apply=_more("education", "edu_institution"), counts=False, section="education", loop="education"),

    Step("has_experience", _p("💼 Avval biror joyda ishlaganmisiz?"), input="choice",
         options=_yes_no("Ha, ishlaganman", "Yo'q, tajribam yo'q"), apply=_apply_experience,
         section="experience"),
    Step("job_company", lambda data: (
        "Keyingi ish joyingiz nomini yozing:" if data.get("jobs") else
        "Qayerda ishlagansiz? Korxona yoki do'kon nomini yozing.\nMasalan: <i>Makro supermarketi</i>"),
        parse=text_parser(2, 100, "Nomi"), apply=_set_item("company"), counts=False, section="experience"),
    Step("job_position", _p("Qaysi lavozimda ishlagansiz?\nMasalan: <i>Sotuvchi</i>"),
         parse=text_parser(2, 100, "Lavozim"), apply=_set_item("position"), counts=False, section="experience"),
    Step("job_start", _p("Qachon ishni boshlagansiz? oy.yil ko'rinishida yozing.\nMasalan: <i>03.2021</i>"),
         parse=parse_month_year, apply=_set_item("start"), counts=False, section="experience"),
    Step("job_end", _p("Qachon ishdan ketgansiz? oy.yil ko'rinishida yozing.\nMasalan: <i>02.2023</i>"),
         input="choice", options=lambda data: [("present", "Hozir ham shu yerda ishlayman")],
         parse=parse_job_end, apply=_apply_job_end, counts=False, section="experience"),
    Step("job_more", lambda data: f"✅ Qo'shildi: <b>{escape(_item_title(data, 'company', 'position'))}</b>"
                                  "\n\nYana ish joyi qo'shasizmi?",
         input="choice", options=_yes_no("➕ Ha, qo'shish", "➡️ Yo'q, davom etish"),
         apply=_more("jobs", "job_company"), counts=False, section="experience", loop="jobs"),

    Step("lang_name", lambda data: (
        "Yana qaysi tilni bilasiz? Tanlang yoki nomini yozing." if data.get("languages") else
        "🗣 Qaysi tillarni bilasiz? Tilni tanlang yoki nomini yozing."),
        input="choice", options=_labels(LANGUAGES), parse=text_parser(2, 30, "Til nomi"),
        apply=_set_item("name"), skip=True, skip_to="computer_skills", section="skills"),
    Step("lang_level", lambda data: f"<b>{escape(_item_title(data, 'name'))}</b> tilini qay darajada bilasiz?",
         input="choice", options=_labels(LANGUAGE_LEVELS), apply=_set_item("level"),
         counts=False, section="skills"),
    Step("lang_more", lambda data: f"✅ Qo'shildi: <b>{escape(_item_title(data, 'name', 'level'))}</b>"
                                   "\n\nYana til qo'shasizmi?",
         input="choice", options=_yes_no("➕ Ha, qo'shish", "➡️ Yo'q, davom etish"),
         apply=_more("languages", "lang_name"), counts=False, section="skills", loop="languages"),
    Step("computer_skills", _p("💻 Qaysi kompyuter dasturlarini bilasiz?\nMasalan: <i>Word, Excel, 1C</i>"),
         parse=text_parser(2, 200, "Javob"), apply=_set("computer_skills"), skip=True, section="skills"),
    Step("driver_license", _p("🚗 Haydovchilik guvohnomangiz bormi? Toifasini tanlang yoki yozing."),
         input="choice", options=_labels(LICENSES), parse=text_parser(1, 30, "Toifa"),
         apply=_apply_license, section="skills"),
    Step("about", _p("✍️ O'zingiz haqingizda qisqacha yozing: xarakteringiz, kuchli tomonlaringiz."),
         parse=text_parser(5, 1000, "Matn"), apply=_set("about"), skip=True, section="skills"),

    Step("preview", _p(""), input="preview", counts=False),
]
STEPS = {step.key: step for step in STEPS_LIST}

COMMON_FLOW = ["consent", "choose"]
FORM_FLOW = [
    "full_name", "birth_date", "gender", "phone", "city", "marital_status", "photo",
    "department", "position", "expected_salary", "start_availability",
    "education_level", "edu_institution", "edu_specialty", "edu_year", "edu_more",
    "has_experience", "job_company", "job_position", "job_start", "job_end", "job_more",
    "lang_name", "lang_level", "lang_more", "computer_skills", "driver_license", "about",
    "preview",
]
UPLOAD_FLOW = ["up_file", "full_name", "phone", "department", "position", "expected_salary", "start_availability",
               "preview"]

SECTIONS = {
    db.KIND_FORM: [("personal", "👤 Shaxsiy ma'lumotlar"), ("position", "💼 Lavozim va maosh"),
                   ("education", "🎓 Ma'lumoti"), ("experience", "🏢 Ish tajribasi"),
                   ("skills", "🗣 Tillar va ko'nikmalar")],
    db.KIND_UPLOAD: [("file", "📎 Rezyume fayli"), ("personal", "👤 Ism va telefon"),
                     ("position", "💼 Lavozim va maosh")],
}
SECTION_LISTS = {"education": ["education"], "experience": ["jobs"], "skills": ["languages"]}


def flow(data: dict) -> list:
    return COMMON_FLOW + (UPLOAD_FLOW if data.get("_kind") == db.KIND_UPLOAD else FORM_FLOW)


def following(data: dict, key: str) -> str:
    keys = flow(data)
    return keys[keys.index(key) + 1]


def progress(data: dict, key: str) -> tuple[int, int] | None:
    """(number, total) of the question, loop questions share the number of their loop."""
    keys = [k for k in flow(data) if k not in COMMON_FLOW and k != "preview"]
    if key not in keys:
        return None
    counted = [k for k in keys if STEPS[k].counts]
    number = 0
    for k in keys[: keys.index(key) + 1]:
        if STEPS[k].counts:
            number += 1
    return max(number, 1), len(counted)


# ===========================
# Draft (the candidate's place in the form)
# ===========================
class Draft:
    def __init__(self, user_id: int, step: str, data: dict):
        self.user_id, self.step, self.data = user_id, step, data

    @classmethod
    async def load(cls, user_id: int):
        row = await db.get_draft(user_id)
        if row is None or row[1] is None:
            return None
        _kind, step, data, _ = row
        if step == "position" and data.get("department_id") is None:
            # A form saved before positions existed, waiting at the old
            # "position" (= department) question: it asks the department now
            step = "department"
        return cls(user_id, step, data)

    async def save(self):
        await db.save_draft(self.user_id, self.data.get("_kind", db.KIND_FORM), self.step, self.data)

    def go(self, next_key: str):
        """Move forward; an edited section ends at the preview."""
        editing = self.data.get("_edit")
        if editing and next_key != "preview" and STEPS[next_key].section != editing:
            next_key = "preview"
        if next_key == "preview":
            self.data.pop("_edit", None)
            self.data["_history"] = []
        else:
            self.data.setdefault("_history", []).append(self.step)
        self.step = next_key

    def back(self) -> bool:
        history = self.data.setdefault("_history", [])
        if not history:
            if self.data.pop("_edit", None):
                self.step = "preview"
                return True
            return False
        previous = history.pop()
        loop = STEPS[previous].loop
        if loop and self.data.get(loop):
            self.data["_item"] = self.data[loop].pop()  # the item is answered again
        self.step = previous
        return True


def _answers(data: dict) -> dict:
    """The candidate's answers without the internal keys."""
    return {key: value for key, value in data.items() if not key.startswith("_")}


# ===========================
# Showing a question
# ===========================
def _button(text: str, step: str, code: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=f"rf:{step}:{code}")


async def _options(step: Step, data: dict) -> list:
    if step.options is None:
        return []
    options = step.options(data)
    return await options if inspect.isawaitable(options) else options


async def _remove(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramAPIError:
        pass  # already deleted, or older than 48 hours


async def _clear_screen(bot: Bot, draft: Draft) -> None:
    """
    Delete the previous question and the helper messages around it, so the chat
    shows one question at a time. The question goes first: it may carry the
    contact keyboard, which must not come back when the helper message that
    removed it is deleted.
    """
    await _remove(bot, draft.user_id, draft.data.pop("_msg", None))
    for message_id in draft.data.pop("_trash", []):
        await _remove(bot, draft.user_id, message_id)


def _contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)],
                  [KeyboardButton(text=BACK_TEXT)]],
        resize_keyboard=True,
    )


async def show_step(bot: Bot, draft: Draft, notice: str | None = None) -> None:
    """Replace the previous question with this one (notice = a line above it, e.g. an error)."""
    await _clear_screen(bot, draft)
    step = STEPS[draft.step]
    if step.input == "preview":
        await show_preview(bot, draft, notice)
        return

    text = step.prompt(draft.data)
    number = progress(draft.data, step.key)
    if number:
        title = "📎 Ariza" if draft.data.get("_kind") == db.KIND_UPLOAD else "📝 Anketa"
        text = f"<b>{title}</b> · {number[0]}/{number[1]}\n\n{text}"
    if notice:
        text = f"{notice}\n\n{text}"

    if step.input == "contact":
        sent = await bot.send_message(draft.user_id, text, reply_markup=_contact_keyboard(), parse_mode="HTML")
        draft.data["_msg"] = sent.message_id
        await draft.save()
        return

    rows = []
    options = await _options(step, draft.data)
    if step.key == "department" and not options:
        text += "\n\n⚠️ Hozircha bo'sh lavozimlar ro'yxati kiritilmagan. Keyinroq /start bosing."
    elif step.key == "position" and not options:
        text += "\n\n⚠️ Bu bo'limda hozir bo'sh lavozim yo'q. Orqaga qaytib boshqa bo'limni tanlang."
    per_row = 1 if any(len(label) > 18 for _, label in options) else 2
    for i in range(0, len(options), per_row):
        rows.append([_button(label, step.key, code) for code, label in options[i: i + per_row]])
    nav = []
    if draft.data.get("_history") or draft.data.get("_edit"):
        nav.append(_button(BACK_TEXT, step.key, "<"))
    if can_skip(step, draft.data):
        label = "➡️ Boshqa yo'q" if step.loop is None and step.skip_to and _has_items(step, draft.data) \
            else "⏭ O'tkazib yuborish"
        nav.append(_button(label, step.key, ">"))
    if nav:
        rows.append(nav)
    sent = await bot.send_message(draft.user_id, text, parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)
    draft.data["_msg"] = sent.message_id
    await draft.save()


# School ("Oʻrta"): the institution may be left out. Higher levels need the first
# institution and its specialty; "skip" there only ends the list ("Boshqa yo'q").
OPTIONAL_INSTITUTION_LEVELS = {EDUCATION_LEVELS[3]}


def can_skip(step: Step, data: dict) -> bool:
    if not step.skip:
        return False
    if (step.key in ("edu_institution", "edu_specialty") and not data.get("education")
            and data.get("education_level") not in OPTIONAL_INSTITUTION_LEVELS):
        return False
    return True


def _has_items(step: Step, data: dict) -> bool:
    lists = {"edu_institution": "education", "lang_name": "languages"}
    return bool(data.get(lists.get(step.key, ""), []))


# ===========================
# Preview and sending
# ===========================
def _preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_button("✅ Adminga yuborish", "preview", "send")],
        [_button("✏️ Tahrirlash", "preview", "edit"), _button("❌ Bekor qilish", "preview", "cancel")],
    ])


def _private_lines(data: dict) -> str:
    department = data.get("department")
    line = (f"🏢 Bo'lim: <b>{escape(department)}</b>\n"
            if department and department != data.get("position") else "")
    return (line + f"💼 Lavozim: <b>{escape(data.get('position') or '—')}</b>\n"
            f"💰 Kutilayotgan maosh: <b>{hr_format.money(data.get('expected_salary'))}</b>\n"
            f"🗓 Ishga chiqish: <b>{escape(data.get('start_availability') or '—')}</b>")


def _fingerprint(data: dict) -> str:
    return hashlib.sha1(json.dumps(_answers(data), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def _download(bot: Bot, file_id: str | None) -> bytes | None:
    if not file_id:
        return None
    try:
        buffer = await bot.download(file_id)
        return buffer.read() if buffer else None
    except Exception:
        logger.exception("Could not download the candidate's photo")
        return None


async def show_preview(bot: Bot, draft: Draft, notice: str | None = None) -> None:
    data = draft.data
    head = f"{notice}\n\n" if notice else ""
    # an older preview (before "Tahrirlash") is replaced by this one
    await _remove(bot, draft.user_id, data.pop("_preview_msg", None))

    async def shown(message) -> None:
        data["_preview_msg"] = message.message_id
        await draft.save()

    if data.get("_kind") == db.KIND_UPLOAD:
        text = (f"{head}📋 <b>Arizangizni tekshiring</b>\n\n"
                f"📎 Rezyume: {escape(data.get('resume_file_name') or 'rasm')}\n"
                f"👤 {escape(data.get('full_name') or '—')}\n"
                f"📞 {escape(data.get('phone') or '—')}\n{_private_lines(data)}")
        await shown(await bot.send_message(draft.user_id, text, reply_markup=_preview_keyboard(),
                                           parse_mode="HTML"))
        return

    caption = (f"{head}📄 <b>Rezyumengiz tayyor!</b> Tekshirib chiqing: hammasi to'g'rimi?\n\n"
               f"Faqat adminga ko'rinadi:\n{_private_lines(data)}\n\n"
               "<i>Bu PDF sizniki — uni boshqa joylarga ham yuborishingiz mumkin.</i>")
    fingerprint = _fingerprint(data)
    if data.get("_pdf_file_id") and data.get("_pdf_fp") == fingerprint:
        await shown(await bot.send_document(draft.user_id, data["_pdf_file_id"], caption=caption,
                                            reply_markup=_preview_keyboard(), parse_mode="HTML"))
        return

    try:
        await bot.send_chat_action(draft.user_id, "upload_document")
    except TelegramAPIError:
        pass
    try:
        form = _answers(data)
        pdf = await render_resume_pdf(form, await _download(bot, data.get("photo_file_id")))
    except ResumeRenderError:
        logger.exception("Resume PDF failed (user_id=%s)", draft.user_id)
        data["_pdf_file_id"] = None
        await shown(await bot.send_message(
            draft.user_id,
            f"{head}⚠️ PDF tayyorlashda xatolik yuz berdi. Ma'lumotlaringiz saqlandi — arizani "
            f"shundayligicha yuborishingiz mumkin, admin ularni ko'radi.\n\n{_private_lines(data)}",
            reply_markup=_preview_keyboard(), parse_mode="HTML",
        ))
        return

    file_name = "Rezyume - " + re.sub(r"[^\w\s.-]", "", data.get("full_name", "")).strip()[:60] + ".pdf"
    message = await bot.send_document(
        draft.user_id, BufferedInputFile(pdf, filename=file_name), caption=caption,
        reply_markup=_preview_keyboard(), parse_mode="HTML",
    )
    data["_pdf_file_id"] = message.document.file_id if message.document else None
    data["_pdf_name"] = file_name
    data["_pdf_fp"] = fingerprint
    await shown(message)


async def _send_application(bot: Bot, callback: types.CallbackQuery, draft: Draft) -> None:
    data = draft.data
    user = callback.from_user
    upload = data.get("_kind") == db.KIND_UPLOAD
    required = ["full_name", "phone", "department_id", "expected_salary"] + (["resume_file_id"] if upload else [])
    if any(data.get(field) is None for field in required):
        await callback.answer("Anketada ma'lumot yetishmayapti. «Tahrirlash» orqali to'ldiring.", show_alert=True)
        return
    await callback.answer()
    app_id = await db.submit_application(
        user.id, user.username,
        kind=db.KIND_UPLOAD if upload else db.KIND_FORM,
        full_name=data["full_name"], phone=data["phone"],
        department_id=data["department_id"], position_id=data.get("position_id"),
        expected_salary=data["expected_salary"],
        start_availability=data.get("start_availability"),
        resume_data=None if upload else _answers(data),
        resume_file_id=data.get("resume_file_id") if upload else data.get("_pdf_file_id"),
        resume_file_name=data.get("resume_file_name") if upload else data.get("_pdf_name"),
        resume_file_type=data.get("resume_file_type") if upload else "document",
    )
    await _drop_buttons(callback)
    if app_id is None:
        await bot.send_message(user.id, PENDING_TEXT)
        return
    await bot.send_message(
        user.id,
        "✅ <b>Arizangiz qabul qilindi!</b>\n\n"
        "Admin uni ko'rib chiqadi va javob shu yerga keladi. Holatini bilish uchun /start bosing.",
        parse_mode="HTML",
    )
    # The application is saved; if the card cannot be delivered, /arizalar still shows it
    try:
        await send_application_card(bot, app_id)
    except TelegramAPIError:
        logger.exception("Could not deliver application #%s to the admin", app_id)


# ===========================
# Taking answers
# ===========================
async def _advance(bot: Bot, draft: Draft, step: Step, value, notice: str | None = None) -> None:
    route = step.apply(draft.data, value) if step.apply else None
    if inspect.isawaitable(route):
        route = await route
    draft.go(route or following(draft.data, step.key))
    await draft.save()
    await show_step(bot, draft, notice)


async def _drop_buttons(callback: types.CallbackQuery, answer: str | None = None) -> None:
    """The answered question loses its buttons (and shows the answer)."""
    try:
        if answer and callback.message.text:
            await callback.message.edit_text(
                callback.message.html_text + f"\n\n<i>Javob:</i> <b>{escape(answer, quote=False)}</b>",
                parse_mode="HTML")
        else:
            await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


async def _start_new(bot: Bot, user_id: int) -> None:
    draft = Draft(user_id, "consent", {})
    await draft.save()
    await show_step(bot, draft)


@router.message(Command("start"), Unapproved())
async def start(message: types.Message, state: FSMContext, bot: Bot):
    await state.clear()
    user_id = message.from_user.id

    active = await db.get_active_application(user_id)
    if active is not None:
        await _show_status(message, active)
        return
    latest = await db.get_latest_application(user_id)
    if latest is not None:
        _, status, decided_at = latest
        if status == db.STATUS_BLOCKED:
            return  # blocked users get no answer at all
        if status in (db.STATUS_REJECTED, db.STATUS_NO_SHOW):
            left = remaining_cooldown(decided_at)
            if left.total_seconds() > 0:
                await message.answer(
                    "❌ Oldingi arizangiz rad etilgan.\n"
                    f"Qayta ariza yuborish uchun {format_duration(left)} kuting."
                )
                return

    draft = await Draft.load(user_id)
    if draft is not None and draft.step not in ("consent", "choose"):
        await message.answer(
            "📝 Sizda tugallanmagan anketa bor. Davom ettirasizmi?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="▶️ Davom ettirish", callback_data="rs:continue"),
                InlineKeyboardButton(text="🔄 Qaytadan boshlash", callback_data="rs:restart"),
            ]]),
        )
        return
    await _start_new(bot, user_id)


async def _show_status(message: types.Message, app: dict) -> None:
    if app["status"] == db.STATUS_INVITED and app["interview_at"]:
        text = ("📅 <b>Siz suhbatga taklif qilingansiz</b>\n\n"
                f"🗓 {hr_format.interview_time(app['interview_at'])}\n"
                f"📍 {escape(app['interview_place'] or '—')}")
    else:
        text = PENDING_TEXT
    await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="↩️ Arizani qaytarib olish", callback_data=f"rw:{app['id']}"),
    ]]))


@router.callback_query(F.data.startswith("rs:"), Unapproved())
async def start_choice(callback: types.CallbackQuery, bot: Bot):
    await _remove(bot, callback.message.chat.id, callback.message.message_id)  # "continue?" is answered
    await callback.answer()
    user_id = callback.from_user.id
    draft = await Draft.load(user_id)
    if callback.data == "rs:continue" and draft is not None:
        await show_step(bot, draft)  # the question moves down, under the /start
    else:
        if draft is not None:
            await _clear_screen(bot, draft)
        await db.delete_draft(user_id)
        await _start_new(bot, user_id)


@router.callback_query(F.data.startswith("rw:"), Unapproved())
async def withdraw(callback: types.CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    app_id = int(parts[1])
    app = await db.get_application_details(app_id)
    if app is None or app["user_id"] != callback.from_user.id:
        await callback.answer()
        return
    if len(parts) == 2:
        await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Ha, qaytarib olaman", callback_data=f"rw:{app_id}:yes"),
            InlineKeyboardButton(text="⬅️ Yo'q", callback_data=f"rw:{app_id}:no"),
        ]]))
        await callback.answer("Arizani rostdan ham qaytarib olasizmi?")
        return
    await _drop_buttons(callback)
    if parts[2] != "yes":
        await callback.answer()
        return
    if not await db.cancel_application(app_id):
        await callback.answer("Bu ariza allaqachon ko'rib chiqilgan.", show_alert=True)
        return
    await callback.answer("Ariza qaytarib olindi")
    await bot.send_message(callback.from_user.id,
                           "↩️ Arizangiz qaytarib olindi. Xohlasangiz, /start bosib yangisini yuborishingiz mumkin.")
    await notify_admin(bot, f"↩️ Ariza #{app_id}: <b>{escape(app['full_name'])}</b> arizasini qaytarib oldi.")
    await refresh_card(bot, app_id)


@router.callback_query(F.data.startswith("rf:"), Unapproved())
async def on_button(callback: types.CallbackQuery, bot: Bot):
    _, key, code = callback.data.split(":", 2)
    draft = await Draft.load(callback.from_user.id)
    if draft is None or draft.step != key or key not in STEPS:
        await callback.answer("Bu tugma eskirgan. Oxirgi savolga javob bering yoki /start bosing.",
                              show_alert=True)
        return
    step = STEPS[key]
    data = draft.data

    if code == "<":
        await callback.answer()
        if draft.back():
            await draft.save()
            await show_step(bot, draft)
        return

    if code == ">" and can_skip(step, data):
        await callback.answer()
        if step.key == "photo":
            data["photo_file_id"] = None
        if step.skip_to:
            data["_item"] = {}
        elif step.apply:
            step.apply(data, None)
        draft.go(step.skip_to or following(data, key))
        await draft.save()
        await show_step(bot, draft)
        return

    if step.input == "preview":
        await _preview_button(callback, bot, draft, code)
        return

    options = dict(await _options(step, data))
    if code not in options:
        await callback.answer("Bu tugma eskirgan.", show_alert=True)
        return
    label = options[code]
    await callback.answer()

    if key == "consent":
        if code == NO:
            await _drop_buttons(callback, label)
            await db.delete_draft(draft.user_id)
            await bot.send_message(draft.user_id, "Tushunarli. Fikringiz o'zgarsa, /start bosing.")
            return
        data["consent_at"] = date.today().isoformat()
        draft.go("choose")
    elif key == "choose":
        data["_kind"] = db.KIND_UPLOAD if code == "upload" else db.KIND_FORM
        draft.go(following(data, "choose"))
    else:
        if key in ("department", "position"):
            value = (int(code), label)
        elif step.loop or key == "has_experience":
            value = code
        elif key == "job_end":
            value = "present"
        else:
            value = label
        await _advance(bot, draft, step, value)
        return
    await draft.save()
    await show_step(bot, draft)


async def _preview_button(callback: types.CallbackQuery, bot: Bot, draft: Draft, code: str) -> None:
    data = draft.data
    if code == "send":
        await _send_application(bot, callback, draft)
    elif code == "edit":
        sections = SECTIONS[data.get("_kind", db.KIND_FORM)]
        rows = [[_button(label, "preview", f"e_{key}")] for key, label in sections]
        rows.append([_button(BACK_TEXT, "preview", "pv")])
        await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await callback.answer("Qaysi qismini o'zgartirasiz?")
    elif code == "cancel":
        await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            _button("🗑 Ha, o'chirilsin", "preview", "cy"), _button("⬅️ Yo'q", "preview", "pv"),
        ]]))
        await callback.answer("Anketa o'chirilsinmi?")
    elif code == "cy":
        await _drop_buttons(callback)
        await db.delete_draft(draft.user_id)
        await callback.answer()
        await bot.send_message(draft.user_id, "🗑 Anketa o'chirildi. Qayta boshlash uchun /start bosing.")
    elif code == "pv":
        await callback.message.edit_reply_markup(reply_markup=_preview_keyboard())
        await callback.answer()
    elif code.startswith("e_"):
        section = code[2:]
        keys = [k for k in flow(data) if STEPS[k].section == section]
        if not keys:
            await callback.answer()
            return
        await _drop_buttons(callback)
        await callback.answer()
        for name in SECTION_LISTS.get(section, []):
            data[name] = []
        data["_item"] = {}
        data["_edit"] = section
        data["_history"] = []
        draft.step = keys[0]
        await draft.save()
        await show_step(bot, draft)
    else:
        await callback.answer()


@router.message(Unapproved())
async def on_message(message: types.Message, bot: Bot):
    draft = await Draft.load(message.from_user.id)
    if draft is None:
        raise SkipHandler()  # handlers/application.py answers people without a form
    step = STEPS[draft.step]
    data = draft.data

    if step.input == "preview":
        await message.answer("Yuqoridagi tugmalardan birini bosing: yuborish, tahrirlash yoki bekor qilish.")
        return

    # Only the current question stays in the chat: the answer itself is removed too
    # (it is saved in the draft and shown in the preview at the end)
    await _remove(bot, message.chat.id, message.message_id)

    async def again(notice: str) -> None:
        await show_step(bot, draft, f"⚠️ {notice}")

    if message.text and message.text.startswith("/"):
        await again("Savolga javob bering yoki anketani boshidan ko'rish uchun /start bosing.")
        return

    if step.input == "contact":
        if message.text == BACK_TEXT:
            hide = await message.answer("↩️", reply_markup=ReplyKeyboardRemove())
            data.setdefault("_trash", []).append(hide.message_id)
            draft.back()
            await draft.save()
            await show_step(bot, draft)
            return
        contact = message.contact
        if contact is None or contact.user_id != message.from_user.id:
            await again("Pastdagi «📱 Telefon raqamni yuborish» tugmasini bosing "
                        "(faqat o'zingizning raqamingiz qabul qilinadi).")
            return
        data["phone"] = "+" + "".join(ch for ch in contact.phone_number if ch.isdigit())
        # the contact keyboard is removed by a message; it is deleted with the question
        hide = await message.answer("✅ Raqam qabul qilindi.", reply_markup=ReplyKeyboardRemove())
        data.setdefault("_trash", []).append(hide.message_id)
        await _advance(bot, draft, step, None, "✅ Raqam qabul qilindi.")
        return

    if step.input == "photo":
        file_id = None
        if message.photo:
            file_id = message.photo[-1].file_id
        elif message.document and (message.document.mime_type or "").startswith("image/"):
            if (message.document.file_size or 0) > MAX_PHOTO_BYTES:
                await again("Rasm juda katta (10 MB gacha bo'lsin).")
                return
            file_id = message.document.file_id
        if file_id is None:
            await again("Rasm yuboring (📎 → Rasm) yoki «O'tkazib yuborish» ni bosing.")
            return
        data["photo_file_id"] = file_id
        await _advance(bot, draft, step, None, "✅ Rasm qabul qilindi.")
        return

    if step.input == "file":
        if message.photo:
            data.update(resume_file_id=message.photo[-1].file_id, resume_file_type="photo",
                        resume_file_name=None)
        elif message.document:
            name = message.document.file_name or ""
            if not name.lower().endswith(UPLOAD_EXTENSIONS):
                await again("Faqat PDF, Word (DOC, DOCX) yoki rasm (JPG, PNG) qabul qilinadi.")
                return
            if (message.document.file_size or 0) > MAX_UPLOAD_BYTES:
                await again("Fayl juda katta. Hajmi 20 MB gacha bo'lsin.")
                return
            data.update(resume_file_id=message.document.file_id, resume_file_type="document",
                        resume_file_name=name)
        else:
            await again("Rezyume faylini yuboring (📎 → Fayl).")
            return
        await _advance(bot, draft, step, None, "✅ Fayl qabul qilindi.")
        return

    if not message.text or step.parse is None:
        await again("Yuqoridagi tugmalardan birini tanlang.")
        return
    try:
        value = step.parse(message.text, data)
    except InputError as error:
        await again(str(error))
        return
    await _advance(bot, draft, step, value)
