"""
Interviews.

Admin (from the card: "📅 Suhbatga taklif qilish" / "📅 Ko'chirish"), one message
edited step by step:
    date (next working days or typed) -> hour -> minutes -> place (a location from
    the catalog or a typed address) -> note (presets, own text or none)
    -> preview of the exact message the candidate gets -> send.
The candidate gets the invitation, the map pin of the place and a
"🔁 Boshqa vaqt so'rash" button: the wish typed after it reaches the admin
with a "📅 Ko'chirish" button.

The admin's wizard keeps its draft in FSM memory (like the catalog wizard): after a
restart its buttons say they are stale and the card button is pressed again.
This router must be included BEFORE handlers/resume.py (candidate catch-all).
"""
import logging
from datetime import date, datetime, timedelta
from html import escape

from aiogram import Bot, F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from config import OWNER_ID
import database as db
from services import hr_format, hr_messages
from .application import Unapproved, notify_admin, refresh_card, send_to_candidate, UNDELIVERED
from .states import InterviewState, RescheduleState

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private")

DAYS_SHOWN = 7
FIRST_HOUR, LAST_HOUR = 8, 19
MINUTES = ["00", "15", "30", "45"]
LAST_HOUR_FOR_TODAY = 17  # after 17:00 today is not offered any more
MAX_DAYS_AHEAD = 90
NOTE_PRESETS = ["Pasport olib keling", "Rasmiy kiyimda keling", "Diplomingizni olib keling"]
WEEKDAYS_SHORT = ["Dush", "Sesh", "Chor", "Pay", "Jum", "Shan", "Yak"]
STALE_TEXT = "Bu tugma eskirgan. Kartadagi tugmani qayta bosing."
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _nav(back_to: str | None) -> list:
    row = [_b("⬅️ Orqaga", f"iv:to:{back_to}")] if back_to else []
    return row + [_b("❌ Bekor qilish", "iv:cancel")]


def working_days(now: datetime) -> list[date]:
    """The next working days (Monday-Saturday); today only while there is time left."""
    day = now.date() if now.hour < LAST_HOUR_FOR_TODAY else now.date() + timedelta(days=1)
    days = []
    while len(days) < DAYS_SHOWN:
        if day.weekday() != 6:
            days.append(day)
        day += timedelta(days=1)
    return days


def parse_day(text: str, today: date) -> date:
    """'27.11' or '27.11.2026' -> a date from today up to MAX_DAYS_AHEAD; ValueError otherwise."""
    parts = text.strip().replace("/", ".").replace("-", ".").split(".")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise ValueError("format")
    day, month = int(parts[0]), int(parts[1])
    year = int(parts[2]) if len(parts) == 3 else today.year
    value = date(year, month, day)
    if len(parts) == 2 and value < today:
        value = date(year + 1, month, day)
    if not today <= value <= today + timedelta(days=MAX_DAYS_AHEAD):
        raise ValueError("range")
    return value


# ===========================
# Screens
# ===========================
def _head(draft: dict) -> str:
    verb = "Suhbatni ko'chirish" if draft["moved"] else "Suhbatga taklif"
    return f"📅 <b>{verb}:</b> {escape(draft['name'])} (#{draft['app_id']})\n\n"


def _chosen(draft: dict) -> str:
    lines = []
    if draft.get("day"):
        day = date.fromisoformat(draft["day"])
        moment = f"{day:%Y-%m-%d} {draft.get('hour', '00')}:{draft.get('minute', '00')}"
        text = hr_format.interview_time(moment)
        lines.append("🗓 " + (text if draft.get("minute") else text.rsplit(", ", 1)[0]))
    if draft.get("place"):
        lines.append(f"📍 {escape(draft['place'])}")
    return ("\n".join(lines) + "\n\n") if lines else ""


async def screen(draft: dict, name: str):
    """(text, markup) of a wizard screen."""
    head = _head(draft) + _chosen(draft)
    if name == "day":
        now = datetime.now()
        rows, row = [], []
        for day in working_days(now):
            label = f"{WEEKDAYS_SHORT[day.weekday()]} {day:%d.%m}"
            if day == now.date():
                label = f"Bugun {day:%d.%m}"
            elif day == now.date() + timedelta(days=1):
                label = f"Ertaga {day:%d.%m}"
            row.append(_b(label, f"iv:day:{day:%Y%m%d}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        rows += [row] if row else []
        rows.append([_b("📆 Boshqa sana", "iv:day:other")])
        rows.append(_nav(None))
        return head + "Suhbat kunini tanlang:", _kb(rows)
    if name == "hour":
        hours = [f"{h:02d}" for h in range(FIRST_HOUR, LAST_HOUR + 1)]
        rows = [[_b(f"{h}:__", f"iv:hour:{h}") for h in hours[i: i + 4]] for i in range(0, len(hours), 4)]
        return head + "Soatni tanlang:", _kb(rows + [_nav("day")])
    if name == "minute":
        rows = [[_b(f"{draft['hour']}:{m}", f"iv:min:{m}") for m in MINUTES]]
        return head + "Daqiqani tanlang:", _kb(rows + [_nav("hour")])
    if name == "place":
        rows = [[_b(f"📍 {loc_name}", f"iv:loc:{loc_id}")]
                for loc_id, loc_name, *_ in await db.list_locations(active_only=True)]
        rows.append([_b("✏️ Boshqa manzil", "iv:loc:other")])
        return head + "Suhbat qayerda bo'ladi?", _kb(rows + [_nav("minute")])
    if name == "note":
        rows = [[_b(note, f"iv:note:{i}")] for i, note in enumerate(NOTE_PRESETS)]
        rows.append([_b("✏️ O'zim yozaman", "iv:note:own"), _b("⏭ Izohsiz", "iv:note:none")])
        return head + "Kadrga izoh qo'shasizmi?", _kb(rows + [_nav("place")])
    # preview
    text = (_head(draft) + "Kadrga quyidagi xabar boradi:\n\n"
            + hr_messages.invitation(draft["name"], _moment(draft), draft["place"], draft.get("lat"),
                                     draft.get("lon"), draft.get("note"), draft["moved"]))
    if draft.get("lat") is not None:
        text += "\n\n<i>+ xaritadagi nuqta</i>"
    return text, _kb([[_b("✅ Yuborish", "iv:send")], _nav("note")])


def _moment(draft: dict) -> str:
    return f"{date.fromisoformat(draft['day']):%Y-%m-%d} {draft['hour']}:{draft['minute']}"


async def _show(bot: Bot, state: FSMContext, name: str, source: types.Message | None = None,
                notice: str | None = None):
    """Draw a screen: edit the pressed message, or (after a typed answer) send a new one."""
    data = await state.get_data()
    draft = data["iv"]
    text, markup = await screen(draft, name)
    if notice:
        text = f"{notice}\n\n{text}"
    await state.set_state(InterviewState.browsing)
    if source is not None:
        try:
            await source.edit_text(text, reply_markup=markup, parse_mode="HTML", link_preview_options=NO_PREVIEW)
        except TelegramBadRequest as error:
            if "not modified" not in str(error):
                raise
        await state.update_data(message_id=source.message_id)
        return
    if data.get("message_id"):
        try:
            await bot.edit_message_reply_markup(chat_id=OWNER_ID, message_id=data["message_id"], reply_markup=None)
        except TelegramBadRequest:
            pass
    sent = await bot.send_message(OWNER_ID, text, reply_markup=markup, parse_mode="HTML",
                                  link_preview_options=NO_PREVIEW)
    await state.update_data(message_id=sent.message_id)


# ===========================
# Admin: the wizard
# ===========================
@router.callback_query(F.data.regexp(r"^hr:(inv|move):\d+$"), F.from_user.id == OWNER_ID)
async def start_wizard(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    _, action, app_id = callback.data.split(":")
    app = await db.get_application_details(int(app_id))
    if app is None or app["status"] not in db.ACTIVE_STATUSES:
        await callback.answer("Bu ariza allaqachon ko'rib chiqilgan.", show_alert=True)
        return
    moved = app["status"] == db.STATUS_INVITED
    await state.clear()
    await state.update_data(iv={"app_id": app["id"], "name": app["full_name"], "moved": moved})
    await _show(bot, state, "day")
    await callback.answer()


async def _draft(callback: types.CallbackQuery, state: FSMContext) -> dict | None:
    draft = (await state.get_data()).get("iv")
    if draft is None:
        await callback.answer(STALE_TEXT, show_alert=True)
    return draft


@router.callback_query(F.data.startswith("iv:"), F.from_user.id == OWNER_ID)
async def wizard_button(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    draft = await _draft(callback, state)
    if draft is None:
        return
    _, action, *rest = callback.data.split(":")
    value = rest[0] if rest else None
    message = callback.message

    if action == "cancel":
        await state.clear()
        await message.edit_text("Bekor qilindi.")
    elif action == "to":
        await _show(bot, state, value, message)
    elif action == "day" and value == "other":
        await state.set_state(InterviewState.entering_date)
        await message.edit_text(_head(draft) + "Sanani kun.oy ko'rinishida yozing. Masalan: <b>27.11</b>",
                                reply_markup=_kb([_nav("day")]), parse_mode="HTML")
    elif action == "day":
        draft["day"] = datetime.strptime(value, "%Y%m%d").date().isoformat()
        draft.pop("minute", None)
        await state.update_data(iv=draft)
        await _show(bot, state, "hour", message)
    elif action == "hour":
        draft["hour"] = value
        draft.pop("minute", None)
        await state.update_data(iv=draft)
        await _show(bot, state, "minute", message)
    elif action == "min":
        draft["minute"] = value
        if _moment(draft) <= datetime.now().strftime("%Y-%m-%d %H:%M"):
            draft.pop("minute", None)
            await state.update_data(iv=draft)
            await callback.answer("Bu vaqt allaqachon o'tib ketgan. Boshqa vaqtni tanlang.", show_alert=True)
            await _show(bot, state, "hour", message)
            return
        await state.update_data(iv=draft)
        await _show(bot, state, "place", message)
    elif action == "loc" and value == "other":
        await state.set_state(InterviewState.entering_place)
        await message.edit_text(_head(draft) + _chosen(draft) + "Manzilni yozing. Masalan: <b>Qarshi, Mustaqillik ko'chasi 5</b>",
                                reply_markup=_kb([_nav("place")]), parse_mode="HTML")
    elif action == "loc":
        location = await db.get_location(int(value))
        if location is None:
            await callback.answer(STALE_TEXT, show_alert=True)
            return
        draft.update(location_id=location[0], place=location[1], lat=location[2], lon=location[3])
        await state.update_data(iv=draft)
        await _show(bot, state, "note", message)
    elif action == "note" and value == "own":
        await state.set_state(InterviewState.entering_note)
        await message.edit_text(_head(draft) + _chosen(draft) + "Izohni yozing:",
                                reply_markup=_kb([_nav("note")]), parse_mode="HTML")
    elif action == "note":
        draft["note"] = None if value == "none" else NOTE_PRESETS[int(value)]
        await state.update_data(iv=draft)
        await _show(bot, state, "preview", message)
    elif action == "send":
        await _send_invitation(callback, state, bot, draft)
        return
    await callback.answer()


@router.message(StateFilter(InterviewState.entering_date), F.from_user.id == OWNER_ID)
async def typed_date(message: types.Message, state: FSMContext, bot: Bot):
    draft = (await state.get_data())["iv"]
    try:
        day = parse_day(message.text or "", date.today())
    except ValueError:
        await message.answer(f"⚠️ Sanani kun.oy ko'rinishida yozing (bugundan {MAX_DAYS_AHEAD} kun ichida). "
                             "Masalan: 27.11")
        return
    draft["day"] = day.isoformat()
    draft.pop("minute", None)
    await state.update_data(iv=draft)
    await _show(bot, state, "hour")


@router.message(StateFilter(InterviewState.entering_place), F.from_user.id == OWNER_ID)
async def typed_place(message: types.Message, state: FSMContext, bot: Bot):
    place = " ".join((message.text or "").split())
    if not 3 <= len(place) <= 200:
        await message.answer("⚠️ Manzilni matn bilan yozing (3–200 ta belgi).")
        return
    draft = (await state.get_data())["iv"]
    draft.update(location_id=None, place=place, lat=None, lon=None)
    await state.update_data(iv=draft)
    await _show(bot, state, "note")


@router.message(StateFilter(InterviewState.entering_note), F.from_user.id == OWNER_ID)
async def typed_note(message: types.Message, state: FSMContext, bot: Bot):
    note = " ".join((message.text or "").split())
    if not 2 <= len(note) <= 300:
        await message.answer("⚠️ Izohni matn bilan yozing (300 ta belgigacha).")
        return
    draft = (await state.get_data())["iv"]
    draft["note"] = note
    await state.update_data(iv=draft)
    await _show(bot, state, "preview")


def candidate_keyboard(app_id: int) -> InlineKeyboardMarkup:
    return _kb([[_b("🔁 Boshqa vaqt so'rash", f"rq:{app_id}")]])


async def send_place_pin(bot: Bot, user_id: int, lat, lon) -> None:
    if lat is None or lon is None:
        return
    try:
        await bot.send_location(user_id, lat, lon)
    except TelegramAPIError:
        logger.warning("Could not send the interview pin (user_id=%s)", user_id)


async def _send_invitation(callback: types.CallbackQuery, state: FSMContext, bot: Bot, draft: dict):
    moment = _moment(draft)
    if moment <= datetime.now().strftime("%Y-%m-%d %H:%M"):
        await callback.answer("Bu vaqt o'tib ketdi. Boshqa vaqtni tanlang.", show_alert=True)
        await _show(bot, state, "day", callback.message)
        return
    app_id = draft["app_id"]
    if not await db.invite_to_interview(app_id, moment, draft["place"], draft.get("location_id"), draft.get("note")):
        await state.clear()
        await callback.message.edit_text("Bu ariza allaqachon ko'rib chiqilgan.")
        await callback.answer()
        return
    await state.clear()
    app = await db.get_application_details(app_id)
    text = hr_messages.invitation(app["full_name"], moment, draft["place"], draft.get("lat"), draft.get("lon"),
                                  draft.get("note"), draft["moved"])
    delivered = await send_to_candidate(bot, app["user_id"], text, candidate_keyboard(app_id))
    if delivered:
        await send_place_pin(bot, app["user_id"], draft.get("lat"), draft.get("lon"))
    verb = "Yangi vaqt yuborildi" if draft["moved"] else "Taklif yuborildi"
    await callback.message.edit_text(
        f"✅ {verb}: <b>{escape(app['full_name'])}</b>\n🗓 {hr_format.interview_time(moment)}\n"
        f"📍 {escape(draft['place'])}" + ("" if delivered else "\n" + UNDELIVERED),
        parse_mode="HTML",
    )
    await callback.answer(verb)
    await refresh_card(bot, app_id)


# ===========================
# Candidate: asking for another time
# ===========================
@router.callback_query(F.data.startswith("rq:"), Unapproved())
async def ask_other_time(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    app = await db.get_application_details(int(parts[1]))
    if app is None or app["user_id"] != callback.from_user.id or app["status"] != db.STATUS_INVITED:
        await callback.answer("Bu tugma eskirgan.", show_alert=True)
        return
    if len(parts) == 3:  # "cancel"
        await state.clear()
        await callback.message.edit_text("Bekor qilindi. Suhbat avvalgi vaqtda bo'ladi.")
        await callback.answer()
        return
    await state.set_state(RescheduleState.waiting)
    await state.update_data(app_id=app["id"])
    await callback.message.answer(
        "Qaysi kun va soat sizga qulay? Qisqacha yozing.\nMasalan: <i>Payshanba, soat 15:00 dan keyin</i>",
        parse_mode="HTML",
        reply_markup=_kb([[_b("❌ Bekor qilish", f"rq:{app['id']}:c")]]),
    )
    await callback.answer()


@router.message(StateFilter(RescheduleState.waiting), Unapproved())
async def other_time_wish(message: types.Message, state: FSMContext, bot: Bot):
    if message.text and message.text.startswith("/"):
        await state.clear()
        raise SkipHandler()  # a command (/start) leaves the request and is handled as usual
    wish = " ".join((message.text or "").split())
    if not 3 <= len(wish) <= 300:
        await message.answer("⚠️ Qulay vaqtni matn bilan qisqacha yozing (300 ta belgigacha).")
        return
    app_id = (await state.get_data())["app_id"]
    await state.clear()
    if not await db.request_reschedule(app_id, wish):
        await message.answer("Bu ariza endi faol emas.")
        return
    app = await db.get_application_details(app_id)
    await message.answer("✅ So'rovingiz adminga yuborildi. Yangi vaqt belgilansa, shu yerga xabar keladi.")
    await notify_admin(
        bot,
        f"🔁 <b>{escape(app['full_name'])}</b> (#{app_id}) suhbat vaqtini o'zgartirishni so'radi:\n"
        f"«{escape(wish)}»\n\nHozirgi vaqt: {hr_format.interview_time(app['interview_at'])}",
        _kb([[_b("📅 Ko'chirish", f"hr:move:{app_id}")]]),
    )
    await refresh_card(bot, app_id)
