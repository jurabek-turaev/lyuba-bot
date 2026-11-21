import logging
from datetime import datetime

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

import database as db
from services import worktime, telegram_group
from services.placement import Location
from .states import AttendanceState, reset_keep_branch

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private")


def get_location_request_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Joylashuvni yuborish", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ===========================
# Keldim — bosilgach, joylashuvni avtomatik so'raydi
# ===========================
@router.message(F.text == "📍 Keldim")
async def ask_location_for_check_in(message: types.Message, state: FSMContext):
    data = await state.get_data()

    name = data.get("name")
    location_id = data.get("location_id")
    video_id = data.get("video_id")

    if not (name and location_id and video_id):
        await message.answer(
            "⚠️ Avval video yuborishingiz kerak.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await reset_keep_branch(state)
        return

    if await db.get_open_attendance_id(message.from_user.id):
        await message.answer("⚠️ Siz allaqachon 'Keldim' deb belgilagansiz.")
        return

    await state.update_data(pending_action="checkin")
    await state.set_state(AttendanceState.waiting_for_location)
    await message.answer(
        "📍 <b>Jonli lokatsiya yuborish talab qilinadi!</b>\n\n"
        "Iltimos, pastdagi skrepka (📎) belgisini bosing ➔ <b>Location</b> (Joylashuv) bo'limiga kiring ➔ "
        "<b>'Share My Live Location' (Jonli lokatsiyani ulashish)</b> tugmasini bosib yuboring.\n\n"
        "<i>⚠️ Statik (oddiy) yoki oldindan saqlangan lokatsiyalar qabul qilinmaydi!</i>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )


# ===========================
# Ketdim — bosilgach, joylashuvni avtomatik so'raydi
# ===========================
@router.message(F.text == "📍 Ketdim")
async def ask_location_for_check_out(message: types.Message, state: FSMContext):
    data = await state.get_data()
    open_id = data.get("open_attendance_id")

    if not open_id:
        await message.answer(
            "⚠️ Sizda ochiq 'Keldim' yozuvi topilmadi.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await reset_keep_branch(state)
        return

    await state.update_data(pending_action="checkout")
    await state.set_state(AttendanceState.waiting_for_location)
    await message.answer(
        "📍 <b>Jonli lokatsiya yuborish talab qilinadi!</b>\n\n"
        "Iltimos, pastdagi skrepka (📎) belgisini bosing ➔ <b>Location</b> (Joylashuv) bo'limiga kiring ➔ "
        "<b>'Share My Live Location' (Jonli lokatsiyani ulashish)</b> tugmasini bosib yuboring.\n\n"
        "<i>⚠️ Statik (oddiy) yoki oldindan saqlangan lokatsiyalar qabul qilinmaydi!</i>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )


# ===========================
# Yakunlash funksiyalari — handlers/location.py dagi handle_location chaqiradi
# (masofa allaqachon tekshirilgan va ruxsat etilgan chegarada)
# ===========================
async def finalize_check_in(message: types.Message, state: FSMContext, distance: int, location: Location):
    data = await state.get_data()
    name = data.get("name")
    branch = location.name

    user_id = message.from_user.id
    work_date = db.today_str()

    if await db.get_open_attendance_id(user_id):
        await message.answer(
            "⚠️ Siz allaqachon 'Keldim' deb belgilagansiz.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await reset_keep_branch(state)
        return

    await db.create_check_in(user_id, name, branch, distance, work_date, location.id)

    # Mahalliy (server TZ) vaqti — bazaga yoziladigan datetime.now() bilan izchil.
    # message.date ishlatilmaydi, chunki u Telegram bergan UTC vaqti bo'ladi.
    time_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    # Guruhga xabar yuborish timeout'da yiqilsa ham, baza allaqachon yozilgan —
    # xodimga tasdiq berish va FSM'ni tozalash BLOKLANMASLIGI kerak. Aks holda
    # xodim tasdiq olmay qayta video yuboradi va holatlar chalkashadi.
    try:
        await telegram_group.send_check_in(message.bot, location.group_id, name, branch, distance, time_str)
    except Exception:
        logger.exception("Guruhga 'Keldi' xabarini yuborib bo'lmadi (branch=%s)", branch)

    await message.answer(
        f"✅ {branch} hududida tasdiqlandi. Kelganingiz qayd etildi.\n"
        "Ishni yakunlaganingizda qayta video yuboring.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await reset_keep_branch(state)


async def finalize_check_out(message: types.Message, state: FSMContext, distance: int, location: Location):
    data = await state.get_data()
    name = data.get("name")
    branch = location.name
    open_id = data.get("open_attendance_id")

    check_out_time = await db.create_check_out(open_id, distance) if open_id else None
    if check_out_time is None:
        # Auto-close already closed this shift while the employee was sending the location
        await message.answer(
            "⚠️ Bu smena allaqachon yopilgan (tizim tomonidan avtomatik yopilgan bo'lishi mumkin).",
            reply_markup=ReplyKeyboardRemove(),
        )
        await reset_keep_branch(state)
        return

    check_in_time, _ = await db.get_attendance_times(open_id)
    hours = worktime.calculate_hours(check_in_time, check_out_time)
    await db.save_work_hours(open_id, hours)

    # Mahalliy (server TZ) vaqti — bazadagi datetime.now() bilan izchil.
    time_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    # Guruhga xabar timeout'da yiqilsa ham, chiqish allaqachon bazaga yozilgan —
    # xodimga tasdiq berish va FSM'ni tozalash BLOKLANMASLIGI kerak.
    try:
        await telegram_group.send_check_out(message.bot, location.group_id, name, branch, distance, time_str, hours)
    except Exception:
        logger.exception("Guruhga 'Ketdi' xabarini yuborib bo'lmadi (branch=%s)", branch)

    await message.answer(
        f"✅ {branch} hududida tasdiqlandi. Ketganingiz qayd etildi. Rahmat!",
        reply_markup=ReplyKeyboardRemove(),
    )
    await reset_keep_branch(state)