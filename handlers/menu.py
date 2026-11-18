"""
Admin menu: every admin command as a persistent reply keyboard button.

The owner gets the keyboard with /menu. Pressing /start also shows it, but only when
the owner is not an employee (an owner who also works keeps the normal employee /start).
Typed commands keep working exactly as before.

A menu button pressed in the middle of another flow (for example while typing a
salary) abandons that flow first, so the button text is never taken as input.

Commands that need an argument become pick-from-a-list flows:
    /bolim_ozgartir <id>   -> "Bo'limni o'zgartirish" (choose the employee)
    /blokdan_chiqar <id>   -> "Blokdan chiqarish" (choose the blocked person)

This router must be included FIRST in handlers/__init__.py.
"""
from html import escape

from aiogram import Bot, F, Router, types
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import OWNER_ID
import database as db
from . import admin, application, shift

router = Router()
router.message.filter(F.chat.type == "private", F.from_user.id == OWNER_ID)
router.callback_query.filter(F.from_user.id == OWNER_ID)

BTN_TABEL = "📊 Tabel"
BTN_EMPLOYEES = "👥 Xodimlar"
BTN_APPLICATIONS = "📝 Arizalar"
BTN_SHIFT = "🕘 Ish vaqtini belgilash"
BTN_DEPARTMENT = "🏢 Bo'limni o'zgartirish"
BTN_UNBLOCK = "🔓 Blokdan chiqarish"
BTN_HELP = "📖 Yordam"


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TABEL), KeyboardButton(text=BTN_EMPLOYEES)],
            [KeyboardButton(text=BTN_APPLICATIONS), KeyboardButton(text=BTN_SHIFT)],
            [KeyboardButton(text=BTN_DEPARTMENT), KeyboardButton(text=BTN_UNBLOCK)],
            [KeyboardButton(text=BTN_HELP)],
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


@router.message(F.text == BTN_HELP)
async def button_help(message: types.Message, state: FSMContext):
    await state.clear()
    await admin.send_help_message(message)


# ===========================
# "Bo'limni o'zgartirish": choose an employee whose department is cleared
# ===========================
@router.message(F.text == BTN_DEPARTMENT)
async def button_department(message: types.Message, state: FSMContext):
    await state.clear()

    employees = await db.get_active_employees()
    if not employees:
        await message.answer("Hozircha faol xodim yo'q.")
        return

    builder = InlineKeyboardBuilder()
    for user_id, full_name, department in employees:
        label = full_name or f"ID {user_id}"
        if department:
            label = f"{label} — {department}"
        builder.add(types.InlineKeyboardButton(text=label, callback_data=f"resetdept:{user_id}"))
    builder.adjust(1)

    await message.answer(
        "🏢 Bo'limi tozalanadigan xodimni tanlang.\n"
        "<i>Xodim /start bosganda bo'limni qaytadan tanlaydi.</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("resetdept:"))
async def reset_department_chosen(callback: types.CallbackQuery):
    target_id = int(callback.data.split(":")[1])

    employee = await db.get_employee_short(target_id)  # (full_name, department, is_active)
    if employee is None:
        await callback.answer("Xodim topilmadi.", show_alert=True)
        return
    full_name, department, _ = employee

    await db.reset_employee_department(target_id)

    text = f"✅ <b>{escape(full_name or f'ID {target_id}')}</b> uchun bo'lim tozalandi"
    if department:
        text += f" (avval: {escape(department)})"
    await callback.message.edit_text(
        text + ".\nU /start bosib qaytadan tanlashi mumkin.",
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