"""
Employee application flow (approval by the admin).

Applicant side (senders with access "unapproved"):
    /start -> full name -> phone (Telegram contact button) -> summary -> "send"

Admin side (OWNER_ID):
    a card with [Approve] [Reject] [Block] for every application,
    /arizalar lists the pending ones, /blokdan_chiqar <user_id> lifts a block.

Applications live in the database, so the admin buttons keep working after a restart.
This router must be included BEFORE the employee routers (see handlers/__init__.py).
"""
import logging
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from config import OWNER_ID
import database as db
from .states import ApplicationState

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private")

MIN_NAME_LEN = 3
MAX_NAME_LEN = 100
REAPPLY_COOLDOWN = timedelta(hours=24)  # a rejected applicant may apply again after this
MAX_PENDING_SHOWN = 10
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

PENDING_TEXT = "⏳ Arizangiz ko'rib chiqilmoqda. Admin qaror qilgach, javob shu yerga keladi."


class Unapproved(BaseFilter):
    """Passes only for senders that AccessMiddleware marked as "unapproved"."""

    async def __call__(self, event: types.TelegramObject, access: str | None = None) -> bool:
        return access == "unapproved"


# ===========================
# Keyboards and texts
# ===========================
def _phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Arizani yuborish", callback_data="apply:send"),
        InlineKeyboardButton(text="✏️ Qayta kiritish", callback_data="apply:redo"),
    ]])


def _review_keyboard(app_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"review:ok:{app_id}"),
            InlineKeyboardButton(text="❌ Rad etish", callback_data=f"review:no:{app_id}"),
        ],
        [InlineKeyboardButton(text="🚫 Bloklash", callback_data=f"review:ban:{app_id}")],
    ])


def _remaining_cooldown(decided_at: str | None) -> timedelta:
    """Time left before a rejected applicant may apply again (zero when it is over)."""
    if not decided_at:
        return timedelta(0)
    left = datetime.strptime(decided_at, DATETIME_FORMAT) + REAPPLY_COOLDOWN - datetime.now()
    return max(left, timedelta(0))


def _format_duration(delta: timedelta) -> str:
    minutes_total = int(delta.total_seconds() // 60) + 1  # round up
    hours, minutes = divmod(minutes_total, 60)
    return f"{hours} soat {minutes} daqiqa" if hours else f"{minutes} daqiqa"


# ===========================
# Applicant side
# ===========================
@router.message(Command("start"), Unapproved())
async def start_application(message: types.Message, state: FSMContext):
    await state.clear()

    latest = await db.get_latest_application(message.from_user.id)
    if latest is not None:
        _, status, decided_at = latest
        if status == "pending":
            await message.answer(PENDING_TEXT)
            return
        if status == "blocked":
            return  # blocked users get no answer at all
        if status == "rejected":
            left = _remaining_cooldown(decided_at)
            if left > timedelta(0):
                await message.answer(
                    "❌ Oldingi arizangiz rad etilgan.\n"
                    f"Qayta ariza yuborish uchun {_format_duration(left)} kuting."
                )
                return

    await message.answer(
        "Assalomu alaykum! Lyuba korxonasi kadrlar tizimiga xush kelibsiz.\n\n"
        "Botdan foydalanish uchun admin tasdig'i kerak. "
        "Iltimos, <b>Ism va Familiyangizni</b> yozing:",
        parse_mode="HTML",
    )
    await state.set_state(ApplicationState.waiting_for_name)


@router.message(StateFilter(ApplicationState.waiting_for_name), Unapproved())
async def get_applicant_name(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("⚠️ Iltimos, Ism va Familiyangizni MATN ko'rinishida yuboring.")
        return

    full_name = " ".join(message.text.split())

    if full_name.startswith("/"):
        await message.answer("⚠️ Ism noto'g'ri. Ism va Familiyangizni yozing.")
        return
    if len(full_name) < MIN_NAME_LEN:
        await message.answer("⚠️ Ism juda qisqa. Ism va Familiyangizni to'liq yozing.")
        return
    if len(full_name) > MAX_NAME_LEN:
        await message.answer("⚠️ Ism juda uzun. Iltimos, faqat Ism va Familiyangizni yozing.")
        return

    await state.update_data(full_name=full_name)
    await state.set_state(ApplicationState.waiting_for_phone)
    await message.answer(
        "📱 Endi telefon raqamingizni yuboring.\n"
        "Pastdagi tugmani bosing (boshqa raqam qabul qilinmaydi).",
        reply_markup=_phone_keyboard(),
    )


@router.message(StateFilter(ApplicationState.waiting_for_phone), F.contact, Unapproved())
async def get_applicant_phone(message: types.Message, state: FSMContext):
    contact = message.contact

    # The shared contact must belong to the sender, otherwise anybody could send a stranger's number
    if contact.user_id != message.from_user.id:
        await message.answer(
            "⚠️ Faqat o'zingizning raqamingizni yuboring (pastdagi tugma orqali).",
            reply_markup=_phone_keyboard(),
        )
        return

    phone = "+" + "".join(ch for ch in contact.phone_number if ch.isdigit())
    await state.update_data(phone=phone)
    await state.set_state(ApplicationState.confirming)
    data = await state.get_data()

    await message.answer("✅ Raqam qabul qilindi.", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        "📋 <b>Ma'lumotlaringiz:</b>\n\n"
        f"👤 {escape(data['full_name'])}\n"
        f"📞 {escape(phone)}\n\n"
        "Arizani adminga yuborasizmi?",
        reply_markup=_confirm_keyboard(),
        parse_mode="HTML",
    )


@router.message(StateFilter(ApplicationState.waiting_for_phone), Unapproved())
async def phone_wrong_input(message: types.Message):
    await message.answer(
        "⚠️ Iltimos, pastdagi «📱 Telefon raqamni yuborish» tugmasini bosing.",
        reply_markup=_phone_keyboard(),
    )


@router.message(StateFilter(ApplicationState.confirming), Unapproved())
async def confirming_hint(message: types.Message):
    await message.answer("Iltimos, yuqoridagi tugmalardan birini bosing.")


@router.callback_query(StateFilter(ApplicationState.confirming), F.data == "apply:redo", Unapproved())
async def redo_application(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ApplicationState.waiting_for_name)
    await callback.message.edit_text("Ism va Familiyangizni qaytadan yozing:")
    await callback.answer()


@router.callback_query(StateFilter(ApplicationState.confirming), F.data == "apply:send", Unapproved())
async def send_application(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    user = callback.from_user

    app_id = await db.create_application(user.id, data["full_name"], data["phone"], user.username)
    await state.clear()

    if app_id is None:
        await callback.message.edit_text(PENDING_TEXT)
        await callback.answer()
        return

    await callback.message.edit_text(
        "✅ Arizangiz yuborildi. Admin ko'rib chiqadi, javob shu yerga keladi."
    )
    await callback.answer()

    # The application is already saved; if the card cannot be delivered, /arizalar still shows it
    try:
        await _send_card(bot, await db.get_application(app_id))
    except TelegramAPIError:
        logger.exception("Could not deliver application #%s to the admin", app_id)


# ===========================
# Admin side
# ===========================
async def _build_card(app) -> str:
    app_id, user_id, full_name, phone, username, _status, created_at = app

    profile = f'<a href="tg://user?id={user_id}">Telegram profili</a> · <code>{user_id}</code>'
    if username:
        profile += f" · @{escape(username)}"

    lines = [
        f"📝 <b>Ariza #{app_id}</b>",
        "",
        f"👤 {escape(full_name)}",
        f"📞 {escape(phone)}",
        f"🆔 {profile}",
        f"🕒 {created_at}",
    ]

    history = await db.get_last_work_info(user_id)
    if history is not None:
        department, last_work_date = history
        line = f"♻️ Avval ishlagan: oxirgi ish kuni {last_work_date}"
        if department:
            line += f", bo'lim: {escape(department)}"
        lines += ["", line]

    return "\n".join(lines)


async def _send_card(bot: Bot, app) -> None:
    await bot.send_message(
        OWNER_ID,
        await _build_card(app),
        reply_markup=_review_keyboard(app[0]),
        parse_mode="HTML",
    )


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
    app = await db.get_application(int(app_id_text))
    if app is None:
        await callback.answer("Ariza topilmadi.", show_alert=True)
        return
    app_id, user_id = app[0], app[1]

    if action == "ok":
        done = await db.approve_application(app_id) is not None
        verdict = "✅ Tasdiqlandi"
        applicant_text = "✅ Arizangiz tasdiqlandi! Botdan foydalanish uchun /start bosing."
    elif action == "no":
        done = await db.decide_application(app_id, "rejected")
        verdict = "❌ Rad etildi"
        applicant_text = "❌ Arizangiz rad etildi. 24 soatdan keyin qayta ariza yuborishingiz mumkin."
    else:  # "ban"
        done = await db.decide_application(app_id, "blocked")
        verdict = "🚫 Bloklandi"
        applicant_text = None  # a blocked user is not told anything

    if not done:
        await callback.answer("Bu ariza allaqachon ko'rib chiqilgan.", show_alert=True)
        return

    note = ""
    if applicant_text is not None and not await _try_send(bot, user_id, applicant_text):
        note = "\n⚠️ Xodimga xabar yetmadi (u botni bloklagan bo'lishi mumkin)."

    await callback.message.edit_text(
        await _build_card(app) + f"\n\n{verdict}{note}",
        parse_mode="HTML",
    )
    await callback.answer(verdict)


@router.message(Command("arizalar"), F.from_user.id == OWNER_ID)
async def list_pending_applications(message: types.Message, bot: Bot):
    pending = await db.get_pending_applications()
    if not pending:
        await message.answer("Kutilayotgan ariza yo'q.")
        return

    for app in pending[:MAX_PENDING_SHOWN]:
        await _send_card(bot, app)

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
    if latest is not None and latest[1] == "pending":
        await message.answer(PENDING_TEXT)
        return
    logger.info("Message from an unapproved user ignored (user_id=%s)", message.from_user.id)


@router.callback_query(Unapproved())
async def unapproved_callback(callback: types.CallbackQuery):
    if callback.data and callback.data.startswith("apply:"):
        await callback.answer("Bu tugma eskirgan. /start ni qayta bosing.", show_alert=True)
    else:
        await callback.answer()