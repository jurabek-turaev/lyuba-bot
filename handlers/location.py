import logging

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import ContentType, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

from config import WORK_LOCATIONS, MAX_DISTANCE
import database as db
from services import gps, telegram_group
from .states import AttendanceState, reset_keep_branch
from .attendance import finalize_check_in, finalize_check_out

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private")

MIN_NAME_LEN = 3
MAX_NAME_LEN = 100


# ===========================
# Tugmalar
# ===========================
def get_checkin_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Keldim")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_checkout_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Ketdim")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_location_request_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Joylashuvni yuborish", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def show_attendance_choice(message: types.Message, state: FSMContext, user_id: int):
    """Xodimning bugungi holatiga qarab faqat Keldim yoki faqat Ketdim tugmasini ko'rsatadi."""
    open_id = await db.get_open_attendance_id(user_id)

    if open_id:
        await state.update_data(open_attendance_id=open_id)
        await db.mark_checkout_requested(open_id)
        await message.answer(
            "Ishni yakunlash uchun tugmani bosing.",
            reply_markup=get_checkout_keyboard(),
        )
        return

    if await db.has_closed_attendance_today(user_id, db.today_str()):
        await message.answer(
            "✅ Siz bugun ish faoliyatingizni allaqachon yakunlagansiz.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await reset_keep_branch(state)
        return

    await message.answer(
        "Ishni boshlash uchun tugmani bosing.",
        reply_markup=get_checkin_keyboard(),
    )


# ===========================
# Video
# ===========================
@router.message(F.content_type == ContentType.VIDEO_NOTE)
async def handle_video(message: types.Message, state: FSMContext):
    data = await state.get_data()
    branch_name = data.get("chosen_branch")

    if not branch_name:
        await message.answer("⚠️ Avval /start orqali filialni tanlang.")
        return

    await state.update_data(video_id=message.video_note.file_id)

    # Video darhol belgilangan guruhga yuboriladi.
    # Guruhga yuborib bo'lmasa ham (bot guruhdan chiqarilgan va h.k.), xodimning
    # jarayoni to'xtab qolmasligi kerak — shuning uchun xatoni ushlab, loglaymiz.
    try:
        await telegram_group.send_video(message.bot, branch_name, message.video_note.file_id)
    except Exception:
        logger.exception("Guruhga video yuborib bo'lmadi (branch=%s)", branch_name)

    existing_name = await db.get_employee_name(message.from_user.id)

    if existing_name is None:
        await message.answer(
            "Siz yangi xodimsiz.\n"
            "Iltimos Ism va Familiyangizni yuboring."
        )
        await state.set_state(AttendanceState.waiting_for_name)
    else:
        await db.update_username(message.from_user.id, message.from_user.username)
        await state.update_data(name=existing_name)
        await message.answer(f"✅ Video qabul qilindi, {existing_name}.")
        await show_attendance_choice(message, state, message.from_user.id)


# ===========================
# Ism (faqat yangi xodim uchun)
# ===========================
@router.message(AttendanceState.waiting_for_name)
async def get_name(message: types.Message, state: FSMContext):
    # Xodim matn o'rniga rasm/stiker/video yuborishi mumkin — bunda message.text = None
    if not message.text:
        await message.answer("⚠️ Iltimos, Ism va Familiyangizni MATN ko'rinishida yuboring.")
        return

    full_name = message.text.strip()

    if len(full_name) < MIN_NAME_LEN:
        await message.answer("⚠️ Ism juda qisqa. Ism va Familiyangizni to'liq yozing.")
        return

    if len(full_name) > MAX_NAME_LEN:
        await message.answer("⚠️ Ism juda uzun. Iltimos, faqat Ism va Familiyangizni yozing.")
        return

    await db.save_employee_name(message.from_user.id, full_name, message.from_user.username)
    await state.update_data(name=full_name)
    await state.set_state(None)

    await message.answer(f"Rahmat, {full_name}!")
    await show_attendance_choice(message, state, message.from_user.id)


# ===========================
# Lokatsiya — Keldim/Ketdim tugmasi bosilgandan KEYIN keladi
# ===========================
@router.message(
    AttendanceState.waiting_for_location,
    F.content_type == ContentType.LOCATION,
)
async def handle_location(message: types.Message, state: FSMContext):
    user_loc = (message.location.latitude, message.location.longitude)
    data = await state.get_data()

    branch_name = data.get("chosen_branch")
    action = data.get("pending_action")

    if not branch_name or branch_name not in WORK_LOCATIONS or not action:
        await message.answer(
            "⚠️ Avval /start orqali qaytadan boshlang.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await reset_keep_branch(state)
        return

    target = WORK_LOCATIONS[branch_name]
    dist = gps.distance_to_branch(user_loc, target)

    if dist > MAX_DISTANCE:
        await message.answer(
            f"❌ Siz {branch_name} hududida emassiz.\n\n"
            f"📏 Masofa: {int(dist)} metr.\n\n"
            "Iltimos, ishxona hududiga yaqinlashib qayta urinib ko'ring.",
            reply_markup=get_location_request_keyboard(),
        )
        return

    if action == "checkin":
        await finalize_check_in(message, state, int(dist))
    else:
        await finalize_check_out(message, state, int(dist))