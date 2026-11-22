"""
Applications: the admin side and the shared pieces of the hiring flow.

The candidate side (consent, resume form / upload, preview, sending) lives in
handlers/resume.py. Here:
    - the admin's card for every application (the resume file with a short summary),
    - [Approve] [Reject] [Block] on the card, /arizalar lists the pending ones,
      /blokdan_chiqar <user_id> lifts a block,
    - the catch-all handlers for users who are not approved (must stay LAST).

Applications live in the database, so the admin buttons keep working after a restart.
This router must be included AFTER handlers/resume.py and BEFORE the employee routers.
"""
import logging
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import OWNER_ID
import database as db
from services import hr_format

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private")

REAPPLY_COOLDOWN = timedelta(days=7)  # a rejected / no-show applicant may apply again after this
MAX_PENDING_SHOWN = 10
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

PENDING_TEXT = "⏳ Arizangiz ko'rib chiqilmoqda. Admin qaror qilgach, javob shu yerga keladi."


class Unapproved(BaseFilter):
    """Passes only for senders that AccessMiddleware marked as "unapproved"."""

    async def __call__(self, event: types.TelegramObject, access: str | None = None) -> bool:
        return access == "unapproved"


def remaining_cooldown(decided_at: str | None) -> timedelta:
    """Time left before a rejected applicant may apply again (zero when it is over)."""
    if not decided_at:
        return timedelta(0)
    left = datetime.strptime(decided_at, DATETIME_FORMAT) + REAPPLY_COOLDOWN - datetime.now()
    return max(left, timedelta(0))


def format_duration(delta: timedelta) -> str:
    minutes_total = int(delta.total_seconds() // 60) + 1  # round up
    hours, minutes = divmod(minutes_total, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days} kun {hours} soat" if hours else f"{days} kun"
    return f"{hours} soat {minutes} daqiqa" if hours else f"{minutes} daqiqa"


# ===========================
# The admin's card
# ===========================
def _review_keyboard(app_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"review:ok:{app_id}"),
            InlineKeyboardButton(text="❌ Rad etish", callback_data=f"review:no:{app_id}"),
        ],
        [InlineKeyboardButton(text="🚫 Bloklash", callback_data=f"review:ban:{app_id}")],
    ])


async def build_card_text(app: dict) -> str:
    """Short HTML summary of an application (fits a document caption: < 1024 chars)."""
    title = f"📝 <b>Ariza #{app['id']}</b>"
    if app["position"]:
        title += f" · {escape(app['position'])}"
    lines = [title, ""]

    form = app["resume_data"] or {}
    person = f"👤 <b>{escape(app['full_name'])}</b>"
    age = hr_format.age(form.get("birth_date"))
    if age is not None:
        person += f", {age} yosh"
    if form.get("city"):
        person += f" · {escape(form['city'])}"
    lines.append(person)

    phone = f"📞 {escape(app['phone'])}"
    if app["username"]:
        phone += f" · @{escape(app['username'])}"
    lines.append(phone)

    if app["kind"] != db.KIND_LEGACY:
        lines.append(f"💰 {hr_format.money(app['expected_salary'])} · "
                     f"🗓 {escape(app['start_availability'] or '—')}")
    if app["kind"] == db.KIND_FORM:
        jobs = form.get("jobs") or []
        experience = "yoʻq"
        if jobs:
            months = hr_format.experience_months(jobs)
            experience = f"{len(jobs)} ish joyi"
            if months:
                experience = f"{hr_format.duration(months)} ({experience})"
        lines.append(f"🎓 {escape(form.get('education_level') or '—')} · 💼 Tajriba: {experience}")
    elif app["kind"] == db.KIND_UPLOAD:
        lines.append(f"📎 Tayyor rezyume: {escape(app['resume_file_name'] or 'rasm')}")

    lines.append(f'🆔 <a href="tg://user?id={app["user_id"]}">Telegram profili</a> · <code>{app["user_id"]}</code>')
    lines.append(f"🕒 {app['created_at']}")

    history = await db.get_last_work_info(app["user_id"])
    if history is not None:
        department, last_work_date = history
        line = f"♻️ Avval ishlagan, qayta topshirmoqda: oxirgi ish kuni {last_work_date}"
        if department:
            line += f", bo'lim: {escape(department)}"
        lines += ["", line]

    return "\n".join(lines)


async def send_application_card(bot: Bot, app_id: int) -> None:
    """Send the card to the admin: the resume file with the summary as its caption."""
    app = await db.get_application_details(app_id)
    text = await build_card_text(app)
    markup = _review_keyboard(app_id)

    message = None
    if app["resume_file_id"]:
        try:
            if app["resume_file_type"] == "photo":
                message = await bot.send_photo(OWNER_ID, app["resume_file_id"], caption=text,
                                               reply_markup=markup, parse_mode="HTML")
            else:
                message = await bot.send_document(OWNER_ID, app["resume_file_id"], caption=text,
                                                  reply_markup=markup, parse_mode="HTML")
        except TelegramAPIError:
            logger.exception("Could not send the resume file of application #%s", app_id)
    if message is None:
        message = await bot.send_message(OWNER_ID, text, reply_markup=markup, parse_mode="HTML")
    await db.set_card_message(app_id, message.message_id)


async def notify_admin(bot: Bot, text: str) -> None:
    try:
        await bot.send_message(OWNER_ID, text, parse_mode="HTML")
    except TelegramAPIError:
        logger.exception("Could not notify the admin")


async def _edit_card(message: types.Message, text: str) -> None:
    """Replace the card text: a caption for a file card, the text otherwise."""
    if message.document or message.photo:
        await message.edit_caption(caption=text, parse_mode="HTML")
    else:
        await message.edit_text(text, parse_mode="HTML")


async def _try_send(bot: Bot, user_id: int, text: str) -> bool:
    """Send a message to the applicant; False when it cannot be delivered (e.g. bot blocked)."""
    try:
        await bot.send_message(user_id, text)
        return True
    except TelegramAPIError:
        logger.warning("Could not message applicant (user_id=%s)", user_id)
        return False


@router.callback_query(F.data.startswith("review:"), F.from_user.id == OWNER_ID)
async def review_application(callback: types.CallbackQuery, bot: Bot):
    _, action, app_id_text = callback.data.split(":")
    app = await db.get_application_details(int(app_id_text))
    if app is None:
        await callback.answer("Ariza topilmadi.", show_alert=True)
        return
    app_id, user_id = app["id"], app["user_id"]

    if action == "ok":
        done = await db.approve_application(app_id) is not None
        verdict = "✅ Tasdiqlandi"
        applicant_text = "✅ Arizangiz tasdiqlandi! Botdan foydalanish uchun /start bosing."
    elif action == "no":
        done = await db.reject_application(app_id)
        verdict = "❌ Rad etildi"
        applicant_text = "❌ Arizangiz rad etildi. 7 kundan keyin qayta ariza yuborishingiz mumkin."
    else:  # "ban"
        done = await db.decide_application(app_id, db.STATUS_BLOCKED)
        verdict = "🚫 Bloklandi"
        applicant_text = None  # a blocked user is not told anything

    if not done:
        await callback.answer("Bu ariza allaqachon ko'rib chiqilgan.", show_alert=True)
        return

    note = ""
    if applicant_text is not None and not await _try_send(bot, user_id, applicant_text):
        note = "\n⚠️ Xodimga xabar yetmadi (u botni bloklagan bo'lishi mumkin)."

    await _edit_card(callback.message, await build_card_text(app) + f"\n\n{verdict}{note}")
    await callback.answer(verdict)


@router.message(Command("arizalar"), F.from_user.id == OWNER_ID)
async def list_pending_applications(message: types.Message, bot: Bot):
    pending = await db.get_pending_applications()
    if not pending:
        await message.answer("Kutilayotgan ariza yo'q.")
        return

    for app in pending[:MAX_PENDING_SHOWN]:
        await send_application_card(bot, app[0])

    if len(pending) > MAX_PENDING_SHOWN:
        await message.answer(
            f"Yana {len(pending) - MAX_PENDING_SHOWN} ta ariza bor. "
            "Avvalgilarini ko'rib chiqqach, /arizalar ni qayta yuboring."
        )


@router.message(Command("blokdan_chiqar"), F.from_user.id == OWNER_ID)
async def unblock_applicant(message: types.Message, command: CommandObject):
    try:
        user_id = int((command.args or "").strip())
    except ValueError:
        await message.answer("⚠️ Foydalanuvchi ID raqam bo'lishi kerak.\nMisol: /blokdan_chiqar 123456789")
        return

    if await db.unblock_user(user_id):
        await message.answer(f"✅ {user_id} blokdan chiqarildi. U /start bosib qayta ariza yuborishi mumkin.")
    else:
        await message.answer("Bu ID bloklanganlar orasida topilmadi.")


# ===========================
# Everything else from unapproved users (must stay LAST in this router)
# ===========================
@router.message(Unapproved())
async def unapproved_message(message: types.Message):
    latest = await db.get_latest_application(message.from_user.id)
    if latest is not None and latest[1] in db.ACTIVE_STATUSES:
        await message.answer(PENDING_TEXT)
        return
    if latest is None or latest[1] != db.STATUS_BLOCKED:
        await message.answer("Ishga ariza topshirish uchun /start bosing.")
        return
    logger.info("Message from a blocked user ignored (user_id=%s)", message.from_user.id)


@router.callback_query(Unapproved())
async def unapproved_callback(callback: types.CallbackQuery):
    if callback.data and callback.data.startswith("apply:"):
        await callback.answer("Bu tugma eskirgan. /start ni qayta bosing.", show_alert=True)
    else:
        await callback.answer()
