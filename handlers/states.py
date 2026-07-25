from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext


class AttendanceState(StatesGroup):
    waiting_for_name = State()
    waiting_for_location = State()


async def reset_keep_branch(state: FSMContext):
    """
    Har bir Keldim/Ketdim sikli tugagach chaqiriladi. state.clear() dan
    farqli o'laroq, tanlangan filialni (chosen_branch) saqlab qoladi —
    shunda xodim har safar /start orqali filialni qayta tanlashi
    shart bo'lmaydi, faqat yangi video/lokatsiya yuborsa yetarli.
    """
    data = await state.get_data()
    await state.set_data({"chosen_branch": data.get("chosen_branch")})
    await state.set_state(None)