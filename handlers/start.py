from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext

import database as db
from services import placement

router = Router()
router.message.filter(F.chat.type == "private")

SEND_VIDEO_TEXT = "Ishga kelganingizni tasdiqlash uchun dumaloq video (video_note) yuboring."
STALE_BUTTON_TEXT = "Bu tugma eskirgan. /start ni qayta bosing."


def get_department_keyboard(departments: list):
    builder = InlineKeyboardBuilder()
    for department in departments:
        builder.add(types.InlineKeyboardButton(text=department.name, callback_data=f"dept:{department.id}"))
    builder.adjust(1)
    return builder.as_markup()


def get_location_keyboard(locations: list):
    builder = InlineKeyboardBuilder()
    for location in locations:
        builder.add(types.InlineKeyboardButton(text=location.name, callback_data=f"loc:{location.id}"))
    builder.adjust(1)
    return builder.as_markup()


def _id_from(callback_data: str) -> int | None:
    """"dept:3" -> 3; None for malformed data."""
    try:
        return int(callback_data.split(":", 1)[1])
    except (IndexError, ValueError):
        return None


async def _ask_department(message: types.Message):
    departments = await placement.list_departments()
    if not departments:
        await message.answer("⚠️ Hozircha bo'limlar sozlanmagan. Iltimos, admin bilan bog'laning.")
        return
    await message.answer(
        "Qaysi bo'limda ishlaysiz? (bu savol faqat birinchi marta beriladi)",
        reply_markup=get_department_keyboard(departments),
    )


async def _offer_locations(message: types.Message, state: FSMContext, department, locations: list, prefix: str):
    """One location: it is chosen automatically. Several: the employee chooses."""
    if not locations:
        await message.answer(
            prefix + f"⚠️ «{department.name}» bo'limiga hali ish joyi biriktirilmagan. "
            "Iltimos, admin bilan bog'laning."
        )
    elif len(locations) == 1:
        await state.update_data(location_id=locations[0].id)
        await message.answer(prefix + SEND_VIDEO_TEXT)
    else:
        await message.answer(
            prefix + "Iltimos, ishlaydigan filialingizni tanlang:",
            reply_markup=get_location_keyboard(locations),
        )


@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()

    department, locations, _ = await placement.employee_placement(message.from_user.id)

    if department is None:
        # Yangi xodim (yoki bo'limi tozalangan / o'chirilgan) — bo'lim tanlanadi
        await message.answer("Assalomu alaykum! Lyuba korxonasi kadrlar tizimiga xush kelibsiz.")
        await _ask_department(message)
        return

    await _offer_locations(message, state, department, locations, f"Assalomu alaykum! ({department.name})\n")


@router.callback_query(F.data.startswith("dept:"))
async def department_chosen(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id

    current, _, _ = await placement.employee_placement(user_id)
    if current is not None:
        # An old department keyboard pressed again: the department is changed only by the admin
        await callback_query.answer(f"Bo'limingiz allaqachon tanlangan: {current.name}", show_alert=True)
        return

    department_id = _id_from(callback_query.data)
    department = await placement.get_department(department_id) if department_id is not None else None
    if department is None:
        await callback_query.answer("Bu bo'lim endi mavjud emas. /start ni qayta bosing.", show_alert=True)
        return

    await db.set_employee_department(user_id, department.id)
    locations = await placement.department_locations(department.id)
    await _offer_locations(
        callback_query.message, state, department, locations, f"✅ Bo'lim saqlandi: {department.name}\n\n"
    )
    await callback_query.answer()


@router.callback_query(F.data.startswith("loc:"))
async def location_chosen(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    location_id = _id_from(callback_query.data)

    _, locations, _ = await placement.employee_placement(user_id)
    location = next((loc for loc in locations if loc.id == location_id), None)
    if location is None:
        await callback_query.answer("Bu filial endi mavjud emas. /start ni qayta bosing.", show_alert=True)
        return

    await state.update_data(location_id=location.id)
    await db.set_employee_location(user_id, location.id)

    await callback_query.message.answer(
        f"✅ Siz {location.name} filialini tanladingiz.\n\n"
        "Endi ishga kelganingizni tasdiqlash uchun dumaloq video (video_note) yuboring."
    )
    await callback_query.answer()


# Keyboards sent before the switch to ids carried names ("dept_Savdo", "branch_Eski bozor")
@router.callback_query(F.data.startswith("dept_") | F.data.startswith("branch_"))
async def stale_name_button(callback_query: types.CallbackQuery):
    await callback_query.answer(STALE_BUTTON_TEXT, show_alert=True)