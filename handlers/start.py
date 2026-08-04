from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext

from config import SAVDO_BRANCHES, FIXED_DEPARTMENTS
import database as db


def test_func():
    pass

router = Router()
router.message.filter(F.chat.type == "private")

DEPARTMENT_OPTIONS = ["Savdo"] + FIXED_DEPARTMENTS


def get_department_keyboard():
    builder = InlineKeyboardBuilder()
    for dept in DEPARTMENT_OPTIONS:
        builder.add(types.InlineKeyboardButton(text=dept, callback_data=f"dept_{dept}"))
    builder.adjust(1)
    return builder.as_markup()


def get_branch_keyboard():
    builder = InlineKeyboardBuilder()
    for branch in SAVDO_BRANCHES:
        builder.add(types.InlineKeyboardButton(text=branch, callback_data=f"branch_{branch}"))
    builder.adjust(1)
    return builder.as_markup()


@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()

    department = await db.get_employee_department(message.from_user.id)

    if department is None:
        # Yangi xodim — bo'lim FAQAT bir marta so'raladi
        await message.answer("Assalomu alaykum! Ishga kelish-ketish tizimiga xush kelibsiz.")
        await message.answer(
            "Qaysi bo'limda ishlaysiz? (bu savol faqat birinchi marta beriladi)",
            reply_markup=get_department_keyboard(),
        )
        return

    if department == "Savdo":
        await message.answer(
            "Iltimos, ishlaydigan do'koningizni tanlang:",
            reply_markup=get_branch_keyboard(),
        )
    else:
        # Ishlab chiqarish / Haydovchi / I.T.R — do'kon tanlash shart emas
        await state.update_data(chosen_branch=department)
        await message.answer(
            f"Assalomu alaykum! ({department})\n"
            "Ishga kelganingizni tasdiqlash uchun dumaloq video (video_note) yuboring."
        )


@router.callback_query(F.data.startswith("dept_"))
async def department_chosen(callback_query: types.CallbackQuery, state: FSMContext):
    department = callback_query.data[len("dept_"):]

    if department not in DEPARTMENT_OPTIONS:
        await callback_query.answer("Noto'g'ri bo'lim.", show_alert=True)
        return

    await db.save_employee_department(callback_query.from_user.id, department)

    if department == "Savdo":
        await callback_query.message.answer(
            f"✅ Bo'lim saqlandi: {department}\n\n"
            "Iltimos, ishlaydigan do'koningizni tanlang:",
            reply_markup=get_branch_keyboard(),
        )
    else:
        await state.update_data(chosen_branch=department)
        await callback_query.message.answer(
            f"✅ Bo'lim saqlandi: {department}\n\n"
            "Ishga kelganingizni tasdiqlash uchun dumaloq video (video_note) yuboring."
        )

    await callback_query.answer()


@router.callback_query(F.data.startswith("branch_"))
async def process_branch_selection(callback_query: types.CallbackQuery, state: FSMContext):
    branch_name = callback_query.data[len("branch_"):]

    if branch_name not in SAVDO_BRANCHES:
        await callback_query.answer("Noto'g'ri filial.", show_alert=True)
        return

    await state.update_data(chosen_branch=branch_name)

    await callback_query.message.answer(
        f"✅ Siz {branch_name} filialini tanladingiz.\n\n"
        "Endi ishga kelganingizni tasdiqlash uchun dumaloq video (video_note) yuboring."
    )
    await callback_query.answer()