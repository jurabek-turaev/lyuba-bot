"""
Applications: the admin side and the shared pieces of the hiring flow.

The candidate side (consent, resume form / upload, preview, sending) lives in
handlers/resume.py. Here:
    - the admin's card for every application (the resume file with a short summary
      and the current status); its buttons follow the status:
        pending: invite to an interview / reject / block
        invited: hire / reject / move the interview / did not come / block
      hire, no-show and block ask for a confirmation; reject: reason -> the letter,
    - "📝 Arizalar" / /arizalar: how many wait in each section (new, waiting for the
      interview, today's interviews); a section button sends its cards,
    - /blokdan_chiqar <user_id> lifts a block,
    - the catch-all handlers for users who are not approved (must stay LAST).

The interview wizard (invite / move) is in handlers/interview.py.

Applications live in the database, so the admin buttons keep working after a restart.
This router must be included AFTER handlers/resume.py and BEFORE the employee routers.
"""
import logging
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot, F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import OWNER_ID
import database as db
from services import hr_format, hr_messages

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
REJECT_REASONS = ["Tajriba yetarli emas", "Maosh kutilmasi yuqori", "Lavozim band",
                  "Suhbatdan o'tmadi", "Boshqa sabab"]


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def card_keyboard(app: dict) -> InlineKeyboardMarkup | None:
    """The card's buttons for the application's current status (none when it is closed)."""
    app_id = app["id"]
    if app["status"] == db.STATUS_PENDING:
        rows = [[_btn("📅 Suhbatga taklif qilish", f"hr:inv:{app_id}")],
                [_btn("❌ Rad etish", f"hr:no:{app_id}"), _btn("🚫 Bloklash", f"hr:ban:{app_id}")]]
        if app["kind"] == db.KIND_LEGACY:  # old name+phone applications: approval without an interview
            rows.insert(0, [_btn("✅ Tasdiqlash", f"hr:hire:{app_id}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    if app["status"] == db.STATUS_INVITED:
        return InlineKeyboardMarkup(inline_keyboard=[
            [_btn("✅ Ishga qabul qilish", f"hr:hire:{app_id}"), _btn("❌ Rad etish", f"hr:no:{app_id}")],
            [_btn("📅 Ko'chirish", f"hr:move:{app_id}"), _btn("🚷 Kelmadi", f"hr:noshow:{app_id}")],
            [_btn("🚫 Bloklash", f"hr:ban:{app_id}")],
        ])
    return None


def status_line(app: dict) -> str:
    status = app["status"]
    if status == db.STATUS_INVITED and app["interview_at"]:
        line = (f"📅 <b>Suhbat:</b> {hr_format.interview_time(app['interview_at'])} · "
                f"{escape(app['interview_place'] or '—')}")
        if app["reschedule_request"]:
            line += f"\n🔁 Kadr boshqa vaqt so'radi: «{escape(app['reschedule_request'][:200])}»"
        return line
    if status == db.STATUS_REJECTED:
        reason = f" · sabab: {escape(app['reject_reason'])}" if app["reject_reason"] else ""
        return f"❌ <b>Rad etildi</b>{reason}"
    return {
        db.STATUS_APPROVED: "✅ <b>Ishga qabul qilindi</b>",
        db.STATUS_NO_SHOW: "🚷 <b>Suhbatga kelmadi</b>",
        db.STATUS_BLOCKED: "🚫 <b>Bloklandi</b>",
        db.STATUS_CANCELLED: "↩️ <b>Kadr arizani qaytarib oldi</b>",
        db.STATUS_UNBLOCKED: "🔓 <b>Blokdan chiqarilgan</b>",
    }.get(status, "")


async def build_card_text(app: dict) -> str:
    """Short HTML summary of an application (fits a document caption: < 1024 chars)."""
    title = f"📝 <b>Ariza #{app['id']}</b>"
    if app["position"]:
        title += f" · {escape(app['position'])}"
        if app["department"] and app["position_id"] is not None and app["department"] != app["position"]:
            title += f" ({escape(app['department'])})"
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

    status = status_line(app)
    if status:
        lines += ["", status]
    return "\n".join(lines)


async def send_application_card(bot: Bot, app_id: int) -> None:
    """Send the card to the admin: the resume file with the summary as its caption."""
    app = await db.get_application_details(app_id)
    text = await build_card_text(app)
    markup = card_keyboard(app)

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


async def _edit_message(bot: Bot, chat_id: int, message_id: int, text: str, markup, media: bool | None):
    """Edit a card; media=None when it is unknown whether the card is a file (caption) or text."""
    attempts = [True, False] if media is None else [media]
    for as_caption in attempts:
        try:
            if as_caption:
                await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=text,
                                               reply_markup=markup, parse_mode="HTML")
            else:
                await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                                            reply_markup=markup, parse_mode="HTML")
            return
        except TelegramBadRequest as error:
            if "not modified" in str(error):
                return
            logger.info("Card #%s not edited as %s: %s", message_id, "caption" if as_caption else "text", error)
        except TelegramAPIError:
            logger.exception("Could not edit card message %s", message_id)
            return


async def refresh_card(bot: Bot, app_id: int, message: types.Message | None = None) -> None:
    """Show the current status and buttons on the card (and on the pressed message, if another one)."""
    app = await db.get_application_details(app_id)
    if app is None:
        return
    text = await build_card_text(app)
    markup = card_keyboard(app)
    if message is not None:
        await _edit_message(bot, message.chat.id, message.message_id, text, markup,
                            bool(message.document or message.photo))
    if app["card_message_id"] and (message is None or message.message_id != app["card_message_id"]):
        await _edit_message(bot, OWNER_ID, app["card_message_id"], text, markup, None)


async def notify_admin(bot: Bot, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await bot.send_message(OWNER_ID, text, parse_mode="HTML", reply_markup=markup)
    except TelegramAPIError:
        logger.exception("Could not notify the admin")


async def send_to_candidate(bot: Bot, user_id: int, text: str, markup=None) -> bool:
    """False when the message cannot be delivered (e.g. the candidate blocked the bot)."""
    try:
        await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=markup,
                               disable_web_page_preview=True)
        return True
    except TelegramAPIError:
        logger.warning("Could not message candidate (user_id=%s)", user_id)
        return False


UNDELIVERED = "⚠️ Kadrga xabar yetmadi (u botni bloklagan bo'lishi mumkin)."


# ===========================
# Card buttons: hire, no-show, block (with a confirmation), reject (reason -> letter)
# ===========================
CONFIRM = {
    "hire": "✅ Ha, ishga qabul qilish",
    "noshow": "🚷 Ha, kelmadi",
    "ban": "🚫 Ha, bloklash",
}


@router.callback_query(F.data.startswith("hr:"), F.from_user.id == OWNER_ID)
async def card_button(callback: types.CallbackQuery, bot: Bot):
    _, action, app_id_text = callback.data.split(":")
    app = await db.get_application_details(int(app_id_text))
    if app is None:
        await callback.answer("Ariza topilmadi.", show_alert=True)
        return
    if action in ("inv", "move"):
        raise SkipHandler()  # the interview wizard (handlers/interview.py)

    if action in CONFIRM:
        if action == "noshow" and app["interview_at"] and \
                app["interview_at"] > datetime.now().strftime("%Y-%m-%d %H:%M"):
            await callback.answer("Suhbat vaqti hali kelmagan.", show_alert=True)
            return
        await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            _btn(CONFIRM[action], f"hr:{action}_ok:{app['id']}"), _btn("⬅️ Yo'q", f"hr:back:{app['id']}"),
        ]]))
        await callback.answer()
    elif action == "back":
        await callback.message.edit_reply_markup(reply_markup=card_keyboard(app))
        await callback.answer()
    elif action == "hire_ok":
        await _hire(callback, bot, app)
    elif action == "noshow_ok":
        await _close(callback, bot, app, await db.mark_no_show(app["id"]), "🚷 Kelmadi deb belgilandi",
                     hr_messages.no_show(app["full_name"]))
    elif action == "ban_ok":
        await _close(callback, bot, app, await db.decide_application(app["id"], db.STATUS_BLOCKED),
                     "🚫 Bloklandi", None)  # a blocked user is not told anything
    elif action == "no":
        await _ask_reject_reason(callback, app)
    else:
        await callback.answer()


async def _close(callback, bot, app, done: bool, verdict: str, candidate_text: str | None):
    if not done:
        await callback.answer("Bu ariza allaqachon ko'rib chiqilgan.", show_alert=True)
        await refresh_card(bot, app["id"], callback.message)
        return
    await callback.answer(verdict)
    await refresh_card(bot, app["id"], callback.message)
    if candidate_text is not None and not await send_to_candidate(bot, app["user_id"], candidate_text):
        await notify_admin(bot, UNDELIVERED)


async def _hire(callback, bot, app):
    if await db.approve_application(app["id"]) is None:
        await callback.answer("Bu ariza allaqachon ko'rib chiqilgan.", show_alert=True)
        await refresh_card(bot, app["id"], callback.message)
        return
    await callback.answer("✅ Ishga qabul qilindi")
    await refresh_card(bot, app["id"], callback.message)
    delivered = await send_to_candidate(bot, app["user_id"], hr_messages.hired(app["full_name"], app["position"]))
    await notify_admin(
        bot,
        f"✅ <b>{escape(app['full_name'])}</b> ishga qabul qilindi"
        + (f" ({escape(app['position'])})" if app["position"] else "") + ".\n"
        "Ish vaqti va maoshini «🕘 Ish vaqtini belgilash» orqali kiriting."
        + ("" if delivered else "\n" + UNDELIVERED),
    )


async def _ask_reject_reason(callback, app, edit: bool = False):
    rows = [[_btn(reason, f"rj:{app['id']}:{i}")] for i, reason in enumerate(REJECT_REASONS)]
    rows.append([_btn("⏭ Sababsiz", f"rj:{app['id']}:x"), _btn("❌ Bekor qilish", f"rj:{app['id']}:c")])
    text = (f"❌ <b>Rad etish:</b> {escape(app['full_name'])} (#{app['id']})\n\n"
            "Sababini tanlang. Sabab kadrga aytilmaydi — faqat ichki statistika uchun saqlanadi.")
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if edit:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await callback.message.answer(text, reply_markup=markup, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("rj:"), F.from_user.id == OWNER_ID)
async def reject_step(callback: types.CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    app = await db.get_application_details(int(parts[1]))
    reason_code = parts[2]
    if app is None or app["status"] not in db.ACTIVE_STATUSES:
        await callback.message.edit_text("Bu ariza allaqachon ko'rib chiqilgan.")
        await callback.answer()
        return
    if reason_code == "c":
        await callback.message.edit_text("Rad etish bekor qilindi.")
        await callback.answer()
        return
    if reason_code == "r":
        await _ask_reject_reason(callback, app, edit=True)
        return
    reason = None if reason_code == "x" else REJECT_REASONS[int(reason_code)]
    letter = hr_messages.rejection(app["full_name"], app["position"])

    if len(parts) == 3:  # reason chosen: show the letter
        await callback.message.edit_text(
            f"❌ <b>Rad etish:</b> {escape(app['full_name'])} (#{app['id']})\n"
            f"Sabab: {escape(reason or 'koʻrsatilmagan')}\n\n"
            f"Kadrga quyidagi xabar boradi:\n\n{letter}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [_btn("✅ Yuborish", f"rj:{app['id']}:{reason_code}:go")],
                [_btn("⬅️ Sababni o'zgartirish", f"rj:{app['id']}:r"), _btn("❌ Bekor qilish", f"rj:{app['id']}:c")],
            ]),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if not await db.reject_application(app["id"], reason):
        await callback.message.edit_text("Bu ariza allaqachon ko'rib chiqilgan.")
        await callback.answer()
        return
    delivered = await send_to_candidate(bot, app["user_id"], letter)
    await callback.message.edit_text(
        f"❌ <b>{escape(app['full_name'])}</b> (#{app['id']}) rad etildi, xat yuborildi."
        + ("" if delivered else "\n" + UNDELIVERED),
        parse_mode="HTML",
    )
    await callback.answer("Rad etildi")
    await refresh_card(bot, app["id"])


@router.callback_query(F.data.startswith("review:"), F.from_user.id == OWNER_ID)
async def old_card_button(callback: types.CallbackQuery, bot: Bot):
    """Cards sent before the hiring flow: their buttons lead to the new actions."""
    _, action, app_id_text = callback.data.split(":")
    callback_data = {"ok": "hire", "no": "no", "ban": "ban"}.get(action, "back")
    app = await db.get_application_details(int(app_id_text))
    if app is None:
        await callback.answer("Ariza topilmadi.", show_alert=True)
        return
    if callback_data == "no":
        await _ask_reject_reason(callback, app)
    elif app["status"] not in db.ACTIVE_STATUSES:
        await callback.answer("Bu ariza allaqachon ko'rib chiqilgan.", show_alert=True)
    else:
        await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            _btn(CONFIRM[callback_data], f"hr:{callback_data}_ok:{app['id']}"),
            _btn("⬅️ Yo'q", f"hr:back:{app['id']}"),
        ]]))
        await callback.answer()


# ===========================
# "📝 Arizalar": applications by section
# ===========================
SECTIONS = {
    "new": "⏳ Yangi",
    "invited": "📅 Suhbat kutilmoqda",
    "today": "📋 Bugungi suhbatlar",
}


async def _section(name: str) -> list:
    """Applications of a section; interviews are sorted by their time."""
    if name == "new":
        return await db.list_applications([db.STATUS_PENDING])
    if name == "invited":
        return sorted(await db.list_applications([db.STATUS_INVITED]), key=lambda app: app["interview_at"] or "")
    today = datetime.now().date()
    return await db.get_interviews_between(f"{today:%Y-%m-%d} 00:00",
                                           f"{today + timedelta(days=1):%Y-%m-%d} 00:00")


@router.message(Command("arizalar"), F.from_user.id == OWNER_ID)
async def list_pending_applications(message: types.Message, bot: Bot):
    """The overview: how many applications wait in each section, a button opens one."""
    counts = {name: len(await _section(name)) for name in SECTIONS}
    lines = ["📝 <b>Arizalar</b>", ""]
    lines += [f"{label}: <b>{counts[name]}</b>" for name, label in SECTIONS.items()]

    today = await _section("today")
    if today:
        lines += ["", "<b>Bugun:</b>"]
        lines += [f"🕙 {app['interview_at'][11:]} — {escape(app['full_name'])}"
                  f" · {escape(app['position'] or '—')} · {escape(app['interview_place'] or '—')}"
                  for app in today]
    if not any(counts.values()):
        lines += ["", "Hozircha javob kutayotgan ariza yo'q."]

    buttons = [[_btn(f"{label} ({counts[name]})", f"al:{name}")] for name, label in SECTIONS.items()]
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                         parse_mode="HTML")


@router.callback_query(F.data.startswith("al:"), F.from_user.id == OWNER_ID)
async def open_section(callback: types.CallbackQuery, bot: Bot):
    name = callback.data.split(":")[1]
    apps = await _section(name) if name in SECTIONS else []
    if not apps:
        await callback.answer("Bu bo'limda ariza yo'q.", show_alert=True)
        return
    await callback.answer()
    for app in apps[:MAX_PENDING_SHOWN]:
        await send_application_card(bot, app["id"])
    if len(apps) > MAX_PENDING_SHOWN:
        await callback.message.answer(
            f"Yana {len(apps) - MAX_PENDING_SHOWN} ta ariza bor. "
            "Avvalgilarini ko'rib chiqqach, bo'limni qayta oching."
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
