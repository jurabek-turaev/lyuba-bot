from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext


class AttendanceState(StatesGroup):
    waiting_for_name = State()
    waiting_for_location = State()


class ApplicationState(StatesGroup):
    """Steps of an employee application (new or removed employee asking for access)."""
    waiting_for_name = State()
    waiting_for_phone = State()
    confirming = State()


class ShiftState(StatesGroup):
    """Admin flow for setting an employee's work schedule and hourly rate."""
    choosing = State()        # inline-button steps: department, employee, time, confirmation
    entering_salary = State()  # waiting for the monthly salary typed as a text message


async def reset_keep_branch(state: FSMContext):
    """
    Har bir Keldim/Ketdim sikli tugagach chaqiriladi. state.clear() dan
    farqli o'laroq, tanlangan filialni (location_id) saqlab qoladi —
    shunda xodim har safar /start orqali filialni qayta tanlashi
    shart bo'lmaydi, faqat yangi video/lokatsiya yuborsa yetarli.
    """
    data = await state.get_data()
    await state.set_data({"location_id": data.get("location_id")})
    await state.set_state(None)