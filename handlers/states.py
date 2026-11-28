from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup


class AttendanceState(StatesGroup):
    waiting_for_name = State()
    waiting_for_location = State()


class CatalogState(StatesGroup):
    """Admin flow for departments and locations (handlers/catalog.py)."""
    browsing = State()        # inline-button steps of a create / edit wizard
    entering_name = State()   # waiting for a name typed as text
    entering_point = State()  # waiting for a Telegram location, a maps link or "lat, lon"


class InterviewState(StatesGroup):
    """Admin: inviting a candidate to an interview or moving it (handlers/interview.py)."""
    browsing = State()        # inline-button steps: date, time, place, note, preview
    entering_date = State()   # a date typed as text
    entering_place = State()  # an address that is not one of the locations
    entering_note = State()   # an own note for the candidate


class RescheduleState(StatesGroup):
    """Candidate: typing the wish for another interview time."""
    waiting = State()


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