"""
Admin menu: every admin command as a persistent reply keyboard button.

The owner gets the keyboard with /menu. Pressing /start also shows it, but only when
the owner is not an employee (an owner who also works keeps the normal employee /start).
Typed commands keep working exactly as before.

A menu button pressed in the middle of another flow (for example while typing a
salary) abandons that flow first, so the button text is never taken as input.

Commands that need an argument become pick-from-a-list flows:
    /bolim_ozgartir <id>   -> "Bo'limni o'zgartirish": choose the employee, then either a new
                              department + position (set by the admin), or "the employee
                              chooses again" (the department is cleared, as /bolim_ozgartir does)
    /blokdan_chiqar <id>   -> "Blokdan chiqarish" (choose the blocked person)

The department/position screens are stateless: every button carries the ids it needs.

This router must be included FIRST in handlers/__init__.py.
"""
from html import escape

from aiogram import Bot, F, Router, types
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

import database as db
from config import OWNER_ID
from services import placement

from . import admin, application, catalog, shift

router = Router()
router.message.filter(F.chat.type == "private", F.from_user.id == OWNER_ID)
router.callback_query.filter(F.from_user.id == OWNER_ID)

BTN_TABEL = "📊 Tabel"
BTN_EMPLOYEES = "👥 Xodimlar"
BTN_APPLICATIONS = "📝 Arizalar"
BTN_SHIFT = "🕘 Ish vaqtini belgilash"
BTN_DEPARTMENT = "🏢 Bo'limni o'zgartirish"
BTN_UNBLOCK = "🔓 Blokdan chiqarish"
BTN_CATALOG = "🗂 Bo'lim va filiallar"
BTN_HELP = "📖 Yordam"


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TABEL), KeyboardButton(text=BTN_EMPLOYEES)],
            [KeyboardButton(text=BTN_APPLICATIONS), KeyboardButton(text=BTN_SHIFT)],
            [KeyboardButton(text=BTN_DEPARTMENT), KeyboardButton(text=BTN_UNBLOCK)],
            [KeyboardButton(text=BTN_CATALOG), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Amalni tanlang",
    )


class OwnerIsNotEmployee(BaseFilter):
    """Passes when the owner has no employee record (a pure admin)."""

    async def __call__(self, message: types.Message) -> bool:
        return not await db.is_employee_approved(message.from_user.id)


# ===========================
# Showing the menu
# ===========================
@router.message(Command("menu"))
async def show_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📋 Admin menyusi. Amalni pastdagi tugmalardan tanlang.",
        reply_markup=admin_menu(),
    )


@router.message(Command("start"), OwnerIsNotEmployee())
async def owner_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Salom, xo'jayin!\n\nAmalni pastdagi tugmalardan tanlang.",
        reply_markup=admin_menu(),
    )


# ===========================
# Buttons that run an existing command
# ===========================
@router.message(F.text == BTN_TABEL)
async def button_tabel(message: types.Message, state: FSMContext):
    await state.clear()
    await admin.send_current_month_tabel(message)


@router.message(F.text == BTN_EMPLOYEES)
async def button_employees(message: types.Message, state: FSMContext):
    await state.clear()
    await admin.list_employees(message)


@router.message(F.text == BTN_APPLICATIONS)
async def button_applications(message: types.Message, state: FSMContext, bot: Bot):
    await state.clear()
    await application.list_pending_applications(message, bot)


@router.message(F.text == BTN_SHIFT)
async def button_shift(message: types.Message, state: FSMContext):
    await state.clear()
    await shift.start_shift_setup(message, state)


@router.message(F.text == BTN_CATALOG)
async def button_catalog(message: types.Message, state: FSMContext):
    await catalog.open_catalog(message, state)


@router.message(F.text == BTN_HELP)
async def button_help(message: types.Message, state: FSMContext):
    await state.clear()
    await admin.send_help_message(message)


# ===========================
# "Bo'limni o'zgartirish": the admin sets an employee's department and position
# ===========================
def _ids(callback_data: str) -> list[int] | None:
    """"jobd:5:3" -> [5, 3]; None for malformed data."""
    try:
        return [int(part) for part in callback_data.split(":")[1:]]
    except ValueError:
        return None


@router.message(F.text == BTN_DEPARTMENT)
async def button_department(message: types.Message, state: FSMContext):
    await state.clear()

    employees = await db.get_active_employees_with_job()
    if not employees:
        await message.answer("Hozircha faol xodim yo'q.")
        return
    missing = {user_id for user_id, *_ in await db.employees_without_position()}

    builder = InlineKeyboardBuilder()
    for user_id, full_name, department, position in employees:
        label = full_name or f"ID {user_id}"
        if department:
            label += f" — {department}"
        if position:
            label += f" · {position}"
        elif user_id in missing:
            label = "⚠️ " + label + " · lavozimsiz"
        builder.add(types.InlineKeyboardButton(text=label, callback_data=f"job:{user_id}"))
    builder.adjust(1)

    text = "🏢 Bo'limi yoki lavozimi o'zgartiriladigan xodimni tanlang."
    if missing:
        text += f"\n\n⚠️ — lavozimi belgilanmagan xodimlar ({len(missing)} ta)."
    await message.answer(text, reply_markup=builder.as_markup())


async def _job_card(user_id: int) -> str | None:
    """The employee's current department and position, or None when there is no such employee."""
    job = await db.get_employee_job(user_id)
    if job is None:
        return None
    full_name, _department_id, department, _position_id, position, _ = job
    return (
        f"👤 <b>{escape(full_name or f'ID {user_id}')}</b>\n"
        f"🏢 Bo'lim: {escape(department or '—')}\n"
        f"💼 Lavozim: {escape(position or '—')}"
    )


@router.callback_query(F.data.startswith("job:"))
async def job_employee_chosen(callback: types.CallbackQuery):
    ids = _ids(callback.data)
    card = await _job_card(ids[0]) if ids else None
    if card is None:
        await callback.answer("Xodim topilmadi.", show_alert=True)
        return
    user_id = ids[0]

    builder = InlineKeyboardBuilder()
    for department in await placement.list_departments():
        builder.add(types.InlineKeyboardButton(text=f"🏢 {department.name}",
                                               callback_data=f"jobd:{user_id}:{department.id}"))
    builder.adjust(1)
    builder.row(types.InlineKeyboardButton(text="🔄 Xodimning o'zi qayta tanlasin",
                                           callback_data=f"resetdept:{user_id}"))
    await callback.message.edit_text(
        card + "\n\nYangi bo'limni tanlang:", reply_markup=builder.as_markup(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("jobd:"))
async def job_department_chosen(callback: types.CallbackQuery):
    ids = _ids(callback.data)
    if not ids or len(ids) != 2:
        await callback.answer("Bu tugma eskirgan.", show_alert=True)
        return
    user_id, department_id = ids
    card = await _job_card(user_id)
    department = await placement.get_department(department_id)
    if card is None or department is None:
        await callback.answer("Xodim yoki bo'lim topilmadi.", show_alert=True)
        return

    positions = await placement.department_positions(department.id)
    if not positions:  # a department without positions: nothing more to ask
        await _save_job(callback, user_id, department, None)
        return

    builder = InlineKeyboardBuilder()
    for position in positions:
        builder.add(types.InlineKeyboardButton(text=f"💼 {position.name}",
                                               callback_data=f"jobp:{user_id}:{position.id}"))
    builder.adjust(2 if all(len(p.name) <= 18 for p in positions) else 1)
    builder.row(types.InlineKeyboardButton(text="◀️ Orqaga", callback_data=f"job:{user_id}"))
    await callback.message.edit_text(
        card + f"\n\nYangi bo'lim: <b>{escape(department.name)}</b>\nLavozimni tanlang:",
        reply_markup=builder.as_markup(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("jobp:"))
async def job_position_chosen(callback: types.CallbackQuery):
    ids = _ids(callback.data)
    if not ids or len(ids) != 2:
        await callback.answer("Bu tugma eskirgan.", show_alert=True)
        return
    user_id, position_id = ids
    position = await placement.get_position(position_id)
    department = await placement.get_department(position.department_id) if position else None
    if department is None or await db.get_employee_job(user_id) is None:
        await callback.answer("Xodim yoki lavozim topilmadi.", show_alert=True)
        return
    await _save_job(callback, user_id, department, position)


async def _save_job(callback: types.CallbackQuery, user_id: int, department, position):
    full_name, _, old_department, _, old_position, _ = await db.get_employee_job(user_id)
    await db.set_employee_job(user_id, department.id, position.id if position else None)

    new = department.name + (f" · {position.name}" if position else "")
    old = " · ".join(part for part in (old_department, old_position) if part)
    text = f"✅ <b>{escape(full_name or f'ID {user_id}')}</b>: {escape(new)}"
    if old and old != new:
        text += f"\n<i>Avval: {escape(old)}</i>"
    if await placement.has_branches(department.id):
        if len(await placement.department_locations(department.id)) > 1:
            text += "\n\n🏪 Savdo filiallari: xodim /start bosganda filialni tanlaydi."
    else:
        base = await placement.get_base_location()
        text += f"\n\n🏭 Ish joyi: {escape(base.name)} (avtomatik)" if base else \
            "\n\n⚠️ Zavod kiritilmagan — xodim ishga kela olmaydi. «🗂 Bo'lim va filiallar» → «🏭 Zavod»."
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer("Saqlandi")


@router.callback_query(F.data.startswith("resetdept:"))
async def reset_department_chosen(callback: types.CallbackQuery):
    target_id = int(callback.data.split(":")[1])

    employee = await db.get_employee_short(target_id)  # (full_name, department, is_active)
    if employee is None:
        await callback.answer("Xodim topilmadi.", show_alert=True)
        return
    full_name, department, _ = employee

    await db.reset_employee_department(target_id)

    text = f"✅ <b>{escape(full_name or f'ID {target_id}')}</b> uchun bo'lim va lavozim tozalandi"
    if department:
        text += f" (avval: {escape(department)})"
    await callback.message.edit_text(
        text + ".\nU /start bosib bo'lim va lavozimni qaytadan tanlaydi.",
        parse_mode="HTML",
    )
    await callback.answer("Bajarildi")


# ===========================
# "Blokdan chiqarish": choose a blocked applicant
# ===========================
@router.message(F.text == BTN_UNBLOCK)
async def button_unblock(message: types.Message, state: FSMContext):
    await state.clear()

    blocked = await db.get_blocked_applicants()
    if not blocked:
        await message.answer("Bloklangan foydalanuvchi yo'q.")
        return

    builder = InlineKeyboardBuilder()
    for user_id, full_name, phone in blocked:
        builder.add(types.InlineKeyboardButton(text=f"{full_name} · {phone}", callback_data=f"unblock:{user_id}"))
    builder.adjust(1)

    await message.answer(
        f"🚫 Bloklanganlar ({len(blocked)} ta).\nBlokdan chiqarish uchun tanlang:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("unblock:"))
async def unblock_chosen(callback: types.CallbackQuery):
    user_id = int(callback.data.split(":")[1])

    latest = await db.get_latest_application(user_id)
    app = await db.get_application(latest[0]) if latest else None
    name = app[2] if app else f"ID {user_id}"

    if not await db.unblock_user(user_id):
        await callback.answer("Bu foydalanuvchi allaqachon blokdan chiqarilgan.", show_alert=True)
        return

    await callback.message.edit_text(
        f"✅ <b>{escape(name)}</b> blokdan chiqarildi.\nU /start bosib qayta ariza yuborishi mumkin.",
        parse_mode="HTML",
    )
    await callback.answer("Bajarildi")