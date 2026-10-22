"""
Admin flow for setting an employee's permanent work schedule and hourly rate.

Command: /ish_vaqti_belgilash (OWNER_ID only, private chat only)

The whole flow lives in ONE message that is edited at every step:
    department -> employee -> start hour -> start minute
    -> end hour -> end minute -> hourly rate (typed) -> confirmation

Callback data format (always starts with "sh:"):
    sh:d:<index>          department (index into the list kept in FSM data)
    sh:e:<user_id>        employee
    sh:t:<kind>:<value>   one part of a time; kind is sh / sm / eh / em
                          (start hour, start minute, end hour, end minute)
    sh:back, sh:cancel, sh:ok
"""
from html import escape

from aiogram import Bot, F, Router, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import OWNER_ID
import database as db
from services.worktime import shift_hours
from .states import ShiftState

router = Router()
router.message.filter(F.chat.type == "private", F.from_user.id == OWNER_ID)
router.callback_query.filter(F.from_user.id == OWNER_ID)

HOURS_PER_ROW = 6
MINUTE_STEP = 5
MINUTES_PER_ROW = 3
# Guards against typing a monthly salary instead of an hourly rate
MAX_HOURLY_RATE = 1_000_000

# Where the "Back" button leads from each step
PREVIOUS_STEP = {
    "emp": "dep",
    "sh": "emp",
    "sm": "sh",
    "eh": "sm",
    "em": "eh",
    "rate": "em",
    "confirm": "rate",
}


# ===========================
# Keyboards and texts
# ===========================
def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _grid(buttons: list, per_row: int) -> list:
    """Split a flat list of buttons into keyboard rows."""
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def _nav_row(with_back: bool = True) -> list:
    row = []
    if with_back:
        row.append(_button("◀️ Orqaga", "sh:back"))
    row.append(_button("❌ Bekor qilish", "sh:cancel"))
    return row


def _hhmm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def _money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")


def _hours_markup(kind: str) -> InlineKeyboardMarkup:
    buttons = [_button(f"{h:02d}", f"sh:t:{kind}:{h}") for h in range(24)]
    return InlineKeyboardMarkup(inline_keyboard=_grid(buttons, HOURS_PER_ROW) + [_nav_row()])


def _minutes_markup(kind: str) -> InlineKeyboardMarkup:
    buttons = [_button(f"{m:02d}", f"sh:t:{kind}:{m}") for m in range(0, 60, MINUTE_STEP)]
    return InlineKeyboardMarkup(inline_keyboard=_grid(buttons, MINUTES_PER_ROW) + [_nav_row()])


def _summary(data: dict) -> str:
    start = _hhmm(data["start_hour"], data["start_minute"])
    end = _hhmm(data["end_hour"], data["end_minute"])
    norm = shift_hours(start, end)
    return (
        f"👤 <b>{escape(data['employee_name'])}</b>\n"
        f"🏢 {escape(data['department'])}\n"
        f"🕘 {start} – {end} (norma: {norm:g} soat/kun)\n"
        f"💰 {_money(data['rate'])} so'm/soat"
    )


async def build_step(step: str, data: dict):
    """Return (text, keyboard) for the given step of the flow."""
    name = escape(data.get("employee_name") or "")

    if step == "dep":
        buttons = [_button(dep, f"sh:d:{i}") for i, dep in enumerate(data["departments"])]
        rows = _grid(buttons, 1) + [_nav_row(with_back=False)]
        return "🏢 <b>Bo'limni tanlang:</b>", InlineKeyboardMarkup(inline_keyboard=rows)

    if step == "emp":
        employees = await db.get_active_employees_by_department(data["department"])
        buttons = []
        for user_id, full_name, shift_start, shift_end in employees:
            label = full_name or f"ID {user_id}"
            if shift_start and shift_end:
                label += f" — {shift_start}–{shift_end}"
            buttons.append(_button(label, f"sh:e:{user_id}"))
        text = f"👥 <b>{escape(data['department'])}</b>\nXodimni tanlang:"
        if not buttons:
            text = f"👥 <b>{escape(data['department'])}</b>\nBu bo'limda faol xodim yo'q."
        rows = _grid(buttons, 1) + [_nav_row()]
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    if step == "sh":
        text = f"🕘 <b>{name}</b>\n\nIsh <b>boshlanish soati</b>ni tanlang:"
        return text, _hours_markup("sh")

    if step == "sm":
        text = (
            f"🕘 <b>{name}</b>\n\n"
            f"Boshlanish: <b>{data['start_hour']:02d}:__</b>\n"
            "Minutni tanlang:"
        )
        return text, _minutes_markup("sm")

    if step == "eh":
        start = _hhmm(data["start_hour"], data["start_minute"])
        text = (
            f"🕘 <b>{name}</b>\n\n"
            f"Boshlanish: <b>{start}</b>\n"
            "Ish <b>tugash soati</b>ni tanlang:"
        )
        return text, _hours_markup("eh")

    if step == "em":
        start = _hhmm(data["start_hour"], data["start_minute"])
        text = (
            f"🕘 <b>{name}</b>\n\n"
            f"Boshlanish: <b>{start}</b>\n"
            f"Tugash: <b>{data['end_hour']:02d}:__</b>\n"
            "Minutni tanlang:"
        )
        return text, _minutes_markup("em")

    if step == "rate":
        text = (
            f"💰 <b>{name}</b>\n\n"
            "Soatlik stavkani so'mda yozib yuboring.\n"
            "Masalan: <code>15000</code>"
        )
        return text, InlineKeyboardMarkup(inline_keyboard=[_nav_row()])

    # step == "confirm"
    text = "📋 <b>Ma'lumotlarni tekshiring:</b>\n\n" + _summary(data) + "\n\nTasdiqlaysizmi?"
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [_button("✅ Tasdiqlash", "sh:ok")],
        _nav_row(),
    ])
    return text, markup


async def _edit_flow_message(bot: Bot, data: dict, text: str, markup=None) -> None:
    """Edit the single message that carries the whole flow."""
    await bot.edit_message_text(
        text=text,
        chat_id=data["chat_id"],
        message_id=data["message_id"],
        reply_markup=markup,
        parse_mode="HTML",
    )


async def show_step(bot: Bot, state: FSMContext, step: str) -> None:
    """Move the flow to the given step and redraw the flow message."""
    await state.update_data(step=step)
    await state.set_state(ShiftState.entering_rate if step == "rate" else ShiftState.choosing)
    data = await state.get_data()
    text, markup = await build_step(step, data)
    await _edit_flow_message(bot, data, text, markup)


# ===========================
# Handlers
# ===========================
@router.message(Command("ish_vaqti_belgilash"))
async def start_shift_setup(message: types.Message, state: FSMContext):
    departments = await db.get_departments()
    if not departments:
        await message.answer("Hozircha bo'limi belgilangan faol xodim yo'q.")
        return

    await state.clear()
    await state.set_state(ShiftState.choosing)
    await state.update_data(departments=departments, step="dep")

    text, markup = await build_step("dep", await state.get_data())
    sent = await message.answer(text, reply_markup=markup, parse_mode="HTML")
    await state.update_data(chat_id=sent.chat.id, message_id=sent.message_id)


@router.callback_query(StateFilter(ShiftState.choosing), F.data.startswith("sh:d:"))
async def on_department(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    index = int(callback.data.split(":")[2])
    departments = data.get("departments", [])

    if data.get("step") != "dep" or index >= len(departments):
        await callback.answer()
        return

    await state.update_data(department=departments[index])
    await show_step(bot, state, "emp")
    await callback.answer()


@router.callback_query(StateFilter(ShiftState.choosing), F.data.startswith("sh:e:"))
async def on_employee(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if data.get("step") != "emp":
        await callback.answer()
        return

    user_id = int(callback.data.split(":")[2])
    employee = await db.get_employee_short(user_id)  # (full_name, department, is_active)
    if employee is None or not employee[2]:
        await callback.answer("Xodim topilmadi yoki ishdan chiqarilgan.", show_alert=True)
        return

    await state.update_data(employee_id=user_id, employee_name=employee[0] or f"ID {user_id}")
    await show_step(bot, state, "sh")
    await callback.answer()


@router.callback_query(StateFilter(ShiftState.choosing), F.data.startswith("sh:t:"))
async def on_time_part(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    _, _, kind, value = callback.data.split(":")
    value = int(value)
    data = await state.get_data()

    # A double tap can deliver a button of an already finished step: ignore it
    if data.get("step") != kind:
        await callback.answer()
        return

    if kind == "sh":
        await state.update_data(start_hour=value)
        next_step = "sm"
    elif kind == "sm":
        await state.update_data(start_minute=value)
        next_step = "eh"
    elif kind == "eh":
        await state.update_data(end_hour=value)
        next_step = "em"
    else:  # kind == "em"
        start = _hhmm(data["start_hour"], data["start_minute"])
        end = _hhmm(data["end_hour"], value)
        if start == end:
            await callback.answer(
                "Boshlanish va tugash vaqti bir xil bo'lishi mumkin emas.",
                show_alert=True,
            )
            return
        await state.update_data(end_minute=value)
        next_step = "rate"

    await show_step(bot, state, next_step)
    await callback.answer()


@router.message(StateFilter(ShiftState.entering_rate), F.text, ~F.text.startswith("/"))
async def on_rate_text(message: types.Message, state: FSMContext, bot: Bot):
    digits = "".join(message.text.split())  # "15 000" -> "15000"
    if not (digits.isascii() and digits.isdigit()) or int(digits) <= 0:
        await message.answer("⚠️ Faqat musbat butun son yozing.\nMasalan: 15000")
        return

    rate = int(digits)
    if rate > MAX_HOURLY_RATE:
        await message.answer(
            f"⚠️ Soatlik stavka {_money(MAX_HOURLY_RATE)} so'mdan oshmasligi kerak.\n"
            "Oylik maosh emas, soatlik stavkani kiriting."
        )
        return

    await state.update_data(rate=rate)
    await show_step(bot, state, "confirm")


@router.callback_query(StateFilter(ShiftState.choosing), F.data == "sh:ok")
async def on_confirm(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if data.get("step") != "confirm":
        await callback.answer()
        return

    start = _hhmm(data["start_hour"], data["start_minute"])
    end = _hhmm(data["end_hour"], data["end_minute"])
    await db.save_work_schedule(data["employee_id"], start, end, data["rate"])

    await state.clear()
    await _edit_flow_message(bot, data, "✅ <b>Saqlandi:</b>\n\n" + _summary(data))
    await callback.answer("Saqlandi")


@router.callback_query(
    StateFilter(ShiftState.choosing, ShiftState.entering_rate),
    F.data == "sh:back",
)
async def on_back(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    previous = PREVIOUS_STEP.get(data.get("step"))
    if previous is None:
        await callback.answer()
        return

    await show_step(bot, state, previous)
    await callback.answer()


@router.callback_query(
    StateFilter(ShiftState.choosing, ShiftState.entering_rate),
    F.data == "sh:cancel",
)
async def on_cancel(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    await _edit_flow_message(bot, data, "Bekor qilindi.")
    await callback.answer()


# Must stay LAST: catches buttons of a flow that no longer exists
# (for example after a bot restart, because FSM data lives in memory).
@router.callback_query(F.data.startswith("sh:"))
async def on_stale_button(callback: types.CallbackQuery):
    await callback.answer(
        "Bu tugma eskirgan. /ish_vaqti_belgilash buyrug'ini qaytadan yuboring.",
        show_alert=True,
    )