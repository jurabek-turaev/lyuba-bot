"""
Admin management of departments and locations (OWNER_ID only, private chat).

Opened with the menu button "🗂 Bo'lim va filiallar" or /filiallar.

Browsing screens (lists, cards, activate / deactivate / delete) are stateless:
everything they need is in the callback data, so they keep working after a restart.
Create / edit wizards keep a "draft" in the FSM data:

    new location:   name -> point -> confirm point -> group -> departments
    new department: name -> locations
    edit:           one field (name / point / group / links)

Callback data always starts with "ct:".
Deleting is allowed only for records nobody ever used; otherwise deactivate.
"""
import logging
from html import escape

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from config import OWNER_ID
import database as db
from services import geo_parse, gps
from .states import CatalogState

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private", F.from_user.id == OWNER_ID)
router.callback_query.filter(F.from_user.id == OWNER_ID)

MIN_NAME_LEN = 2
MAX_NAME_LEN = 40
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
STALE_TEXT = "Bu tugma eskirgan. Menyudan «🗂 Bo'lim va filiallar»ni qayta oching."


# ===========================
# Small helpers
# ===========================
def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _arg(callback_data: str) -> int | None:
    """"ct:loc:5" -> 5; None for malformed data."""
    try:
        return int(callback_data.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


CANCEL_ROW = [_b("❌ Bekor qilish", "ct:cancel")]


async def _edit(message: types.Message, text: str, markup=None):
    try:
        await message.edit_text(text, reply_markup=markup, parse_mode="HTML", link_preview_options=NO_PREVIEW)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise


async def _show(bot: Bot, state: FSMContext, text: str, markup=None, *,
                source: types.Message | None = None, chat_id: int | None = None):
    """
    Draw a wizard screen.
    source: the message whose button was pressed — it is edited in place.
    Otherwise (the admin typed something) the old screen loses its buttons and the
    new screen is sent below the admin's message.
    """
    if source is not None:
        await _edit(source, text, markup)
        await state.update_data(chat_id=source.chat.id, message_id=source.message_id)
        return

    data = await state.get_data()
    if data.get("message_id"):
        try:
            await bot.edit_message_reply_markup(
                chat_id=data["chat_id"], message_id=data["message_id"], reply_markup=None
            )
        except TelegramBadRequest:
            pass
    sent = await bot.send_message(
        chat_id, text, reply_markup=markup, parse_mode="HTML", link_preview_options=NO_PREVIEW
    )
    await state.update_data(chat_id=sent.chat.id, message_id=sent.message_id)


def _clean_name(text: str) -> str:
    return " ".join((text or "").split())


def _map_link(lat: float, lon: float) -> str:
    # Yandex puts LONGITUDE first; pt = a marker on the point
    return f'<a href="https://yandex.uz/maps/?pt={lon},{lat}&amp;z=17&amp;l=map">xaritada ochish</a>'


async def _group_label(group_id: int | None) -> str:
    if group_id is None:
        return "⚠️ biriktirilmagan"
    group = await db.get_group(group_id)
    if group is None:
        return f"ID {group_id}"
    chat_id, title, is_member = group
    label = escape(title) if title else f"ID {chat_id}"
    if not is_member:
        label += " (⚠️ bot bu guruhda emas)"
    return label


async def _refresh_group_titles(bot: Bot):
    """Groups seeded from .env have no title yet: ask Telegram for it."""
    for chat_id, title, _ in await db.list_groups():
        if title:
            continue
        try:
            chat = await bot.get_chat(chat_id)
            await db.upsert_group(chat_id, chat.title, True)
        except (TelegramBadRequest, TelegramForbiddenError):
            await db.upsert_group(chat_id, None, False)  # the bot is not in that group
        except TelegramNetworkError:
            pass


async def _delete_pin(bot: Bot, draft: dict):
    """
    Remove the map preview sent for the point confirmation. It is only needed
    while the admin decides; afterwards it just clutters the chat.
    """
    message_id = draft.pop("pin_message_id", None)
    chat_id = draft.pop("pin_chat_id", None)
    if message_id is None:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        pass  # already deleted by the admin, or older than 48 hours


async def _departments_losing_last_location(location_id: int, keep_ids=None) -> list:
    """
    Names of active departments with active employees whose ONLY active location is
    this one, and which would lose it (keep_ids = departments that keep the link).
    Their employees would have nowhere to check in, so such changes are refused.
    """
    names = []
    for department_id, name, is_active in await db.get_location_departments(location_id):
        if not is_active or (keep_ids is not None and department_id in keep_ids):
            continue
        active_employees, _ = await db.department_usage(department_id)
        if not active_employees:
            continue
        others = [row for row in await db.get_department_locations(department_id) if row[0] != location_id]
        if not others:
            names.append(name)
    return names


async def _name_error(kind: str, name: str, exclude_id: int | None) -> str | None:
    if name.startswith("/"):
        return "⚠️ Nom «/» bilan boshlanmasin."
    if len(name) < MIN_NAME_LEN:
        return "⚠️ Nom juda qisqa."
    if len(name) > MAX_NAME_LEN:
        return f"⚠️ Nom {MAX_NAME_LEN} belgidan oshmasin."
    table = "locations" if kind == "loc" else "departments"
    if await db.catalog_name_taken(table, name, exclude_id):
        return "⚠️ Bu nom band. Boshqa nom yozing."
    return None


# ===========================
# Browsing screens: (text, markup), built from the database only
# ===========================
async def home_screen():
    departments = await db.list_departments(active_only=False)
    locations = await db.list_locations(active_only=False)
    text = (
        "🗂 <b>Bo'lim va filiallar</b>\n\n"
        f"🏢 Bo'limlar: {sum(1 for d in departments if d[2])} faol / {len(departments)}\n"
        f"📍 Filiallar: {sum(1 for loc in locations if loc[5])} faol / {len(locations)}"
    )
    return text, _kb([
        [_b("🏢 Bo'limlar", "ct:deps"), _b("📍 Filiallar", "ct:locs")],
        [_b("✖️ Yopish", "ct:close")],
    ])


async def locations_screen():
    rows = [
        [_b(("📍 " if is_active else "🚫 ") + name, f"ct:loc:{location_id}")]
        for location_id, name, _lat, _lon, _group, is_active in await db.list_locations(active_only=False)
    ]
    rows += [[_b("➕ Yangi filial", "ct:newloc")], [_b("◀️ Orqaga", "ct:home")]]
    return "📍 <b>Filiallar</b>\n<i>🚫 — faol emas</i>", _kb(rows)


async def location_screen(location_id: int):
    row = await db.get_location(location_id)
    if row is None:
        return None
    _, name, lat, lon, group_id, is_active = row
    attendance_count, open_shifts, chosen_by = await db.location_usage(location_id)
    departments = await db.get_location_departments(location_id)
    department_names = ", ".join(escape(d[1]) + ("" if d[2] else " 🚫") for d in departments) or "—"

    text = (
        f"📍 <b>{escape(name)}</b> — {'✅ faol' if is_active else '🚫 faol emas'}\n\n"
        f"🌐 <code>{lat}, {lon}</code> · {_map_link(lat, lon)}\n"
        f"👥 Guruh: {await _group_label(group_id)}\n"
        f"🏢 Bo'limlar: {department_names}\n"
        f"📊 Davomat yozuvlari: {attendance_count} · ochiq smena: {open_shifts}"
    )
    rows = [
        [_b("✏️ Nomi", f"ct:lname:{location_id}"), _b("🌐 Joylashuvi", f"ct:lpoint:{location_id}")],
        [_b("👥 Guruhi", f"ct:lgroup:{location_id}"), _b("🏢 Bo'limlari", f"ct:ldeps:{location_id}")],
        [_b("🚫 Faolsizlantirish", f"ct:loff:{location_id}") if is_active
         else _b("✅ Faollashtirish", f"ct:lon:{location_id}")],
    ]
    if attendance_count == 0 and chosen_by == 0:
        rows.append([_b("🗑 O'chirish", f"ct:ldel:{location_id}")])
    rows.append([_b("◀️ Filiallar", "ct:locs")])
    return text, _kb(rows)


async def departments_screen():
    rows = [
        [_b(("🏢 " if is_active else "🚫 ") + name, f"ct:dep:{department_id}")]
        for department_id, name, is_active in await db.list_departments(active_only=False)
    ]
    rows += [[_b("➕ Yangi bo'lim", "ct:newdep")], [_b("◀️ Orqaga", "ct:home")]]
    return "🏢 <b>Bo'limlar</b>\n<i>🚫 — faol emas</i>", _kb(rows)


async def department_screen(department_id: int):
    row = await db.get_department(department_id)
    if row is None:
        return None
    _, name, is_active = row
    active_employees, all_employees = await db.department_usage(department_id)
    locations = await db.get_department_locations(department_id, active_only=False)
    active_locations = [loc for loc in locations if loc[5]]
    location_names = ", ".join(escape(loc[1]) + ("" if loc[5] else " 🚫") for loc in locations) or "—"

    if not active_locations:
        rule = "⚠️ Faol filial yo'q — xodimlar ishga kela olmaydi."
    elif len(active_locations) == 1:
        rule = "ℹ️ Bitta filial — xodimga avtomatik tanlanadi."
    else:
        rule = "ℹ️ Bir nechta filial — xodim har safar o'zi tanlaydi."

    text = (
        f"🏢 <b>{escape(name)}</b> — {'✅ faol' if is_active else '🚫 faol emas'}\n\n"
        f"👥 Faol xodimlar: {active_employees}\n"
        f"📍 Filiallar: {location_names}\n{rule}"
    )
    rows = [
        [_b("✏️ Nomi", f"ct:dname:{department_id}"), _b("📍 Filiallari", f"ct:dlocs:{department_id}")],
        [_b("🚫 Faolsizlantirish", f"ct:doff:{department_id}") if is_active
         else _b("✅ Faollashtirish", f"ct:don:{department_id}")],
    ]
    if all_employees == 0:
        rows.append([_b("🗑 O'chirish", f"ct:ddel:{department_id}")])
    rows.append([_b("◀️ Bo'limlar", "ct:deps")])
    return text, _kb(rows)


async def _screen_for(target: str):
    """Screen named by a "back" target such as "ct:locs" or "ct:loc:5"."""
    if target.startswith("ct:loc:"):
        screen = await location_screen(_arg(target))
        return screen or await locations_screen()
    if target.startswith("ct:dep:"):
        screen = await department_screen(_arg(target))
        return screen or await departments_screen()
    if target == "ct:locs":
        return await locations_screen()
    if target == "ct:deps":
        return await departments_screen()
    return await home_screen()


# ===========================
# Opening and browsing
# ===========================
@router.message(Command("filiallar"))
async def open_catalog(message: types.Message, state: FSMContext):
    await state.clear()
    await _refresh_group_titles(message.bot)  # cards show group titles, not bare ids
    text, markup = await home_screen()
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.in_({"ct:home", "ct:locs", "ct:deps"}))
async def browse_list(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await _edit(callback.message, *await _screen_for(callback.data))
    await callback.answer()


@router.callback_query(F.data.startswith("ct:loc:") | F.data.startswith("ct:dep:"))
async def browse_card(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await _edit(callback.message, *await _screen_for(callback.data))
    await callback.answer()


@router.callback_query(F.data == "ct:close")
async def close_catalog(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await _edit(callback.message, "🗂 Bo'lim va filiallar yopildi.")
    await callback.answer()


# ===========================
# Activate / deactivate / delete (stateless, with confirmation)
# ===========================
@router.callback_query(F.data.startswith("ct:loff:"))
async def location_deactivate_ask(callback: types.CallbackQuery):
    location_id = _arg(callback.data)
    row = await db.get_location(location_id)
    if row is None:
        await callback.answer("Filial topilmadi.", show_alert=True)
        return
    blocked = await _departments_losing_last_location(location_id)
    if blocked:
        await callback.answer(
            "Faolsizlantirib bo'lmaydi: bu — quyidagi bo'lim(lar)ning yagona filiali, "
            f"ularda faol xodimlar bor: {', '.join(blocked)}.\n"
            "Avval bo'limga boshqa filial biriktiring.",
            show_alert=True,
        )
        return
    await _edit(
        callback.message,
        f"🚫 <b>{escape(row[1])}</b> faolsizlantirilsinmi?\n\n"
        "• Xodimlar uni endi tanlay olmaydi.\n"
        "• Ochiq smenalar shu filialda odatdagidek yopiladi.\n"
        "• Davomat tarixi saqlanadi, keyin qayta faollashtirish mumkin.",
        _kb([[_b("✅ Ha", f"ct:loffok:{location_id}"), _b("❌ Yo'q", f"ct:loc:{location_id}")]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ct:loffok:"))
async def location_deactivate(callback: types.CallbackQuery):
    location_id = _arg(callback.data)
    blocked = await _departments_losing_last_location(location_id)
    if blocked:  # something changed since the question
        await callback.answer(f"Faolsizlantirib bo'lmaydi: {', '.join(blocked)}", show_alert=True)
        return
    await db.set_location_active(location_id, False)
    await _edit(callback.message, *await _screen_for(f"ct:loc:{location_id}"))
    await callback.answer("Faolsizlantirildi")


@router.callback_query(F.data.startswith("ct:lon:"))
async def location_activate(callback: types.CallbackQuery):
    location_id = _arg(callback.data)
    await db.set_location_active(location_id, True)
    await _edit(callback.message, *await _screen_for(f"ct:loc:{location_id}"))
    await callback.answer("Faollashtirildi")


@router.callback_query(F.data.startswith("ct:ldel:"))
async def location_delete_ask(callback: types.CallbackQuery):
    location_id = _arg(callback.data)
    row = await db.get_location(location_id)
    if row is None:
        await callback.answer("Filial topilmadi.", show_alert=True)
        return
    await _edit(
        callback.message,
        f"🗑 <b>{escape(row[1])}</b> butunlay o'chirilsinmi?\n<i>Bu amalni qaytarib bo'lmaydi.</i>",
        _kb([[_b("🗑 Ha, o'chirish", f"ct:ldelok:{location_id}"), _b("❌ Yo'q", f"ct:loc:{location_id}")]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ct:ldelok:"))
async def location_delete(callback: types.CallbackQuery):
    location_id = _arg(callback.data)
    if not await db.delete_location(location_id):
        await callback.answer(
            "O'chirib bo'lmaydi: filial davomatda ishlatilgan. Faolsizlantirish mumkin.", show_alert=True
        )
        await _edit(callback.message, *await _screen_for(f"ct:loc:{location_id}"))
        return
    await _edit(callback.message, *await locations_screen())
    await callback.answer("O'chirildi")


@router.callback_query(F.data.startswith("ct:doff:"))
async def department_deactivate_ask(callback: types.CallbackQuery):
    department_id = _arg(callback.data)
    row = await db.get_department(department_id)
    if row is None:
        await callback.answer("Bo'lim topilmadi.", show_alert=True)
        return
    active_employees, _ = await db.department_usage(department_id)
    if active_employees:
        await callback.answer(
            f"Faolsizlantirib bo'lmaydi: bo'limda {active_employees} ta faol xodim bor.\n"
            "Avval ularni boshqa bo'limga o'tkazing yoki ishdan chiqaring.",
            show_alert=True,
        )
        return
    await _edit(
        callback.message,
        f"🚫 <b>{escape(row[1])}</b> bo'limi faolsizlantirilsinmi?\n"
        "Yangi xodimlar uni tanlay olmaydi. Keyin qayta faollashtirish mumkin.",
        _kb([[_b("✅ Ha", f"ct:doffok:{department_id}"), _b("❌ Yo'q", f"ct:dep:{department_id}")]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ct:doffok:"))
async def department_deactivate(callback: types.CallbackQuery):
    department_id = _arg(callback.data)
    active_employees, _ = await db.department_usage(department_id)
    if active_employees:
        await callback.answer(f"Bo'limda {active_employees} ta faol xodim bor.", show_alert=True)
        return
    await db.set_department_active(department_id, False)
    await _edit(callback.message, *await _screen_for(f"ct:dep:{department_id}"))
    await callback.answer("Faolsizlantirildi")


@router.callback_query(F.data.startswith("ct:don:"))
async def department_activate(callback: types.CallbackQuery):
    department_id = _arg(callback.data)
    await db.set_department_active(department_id, True)
    await _edit(callback.message, *await _screen_for(f"ct:dep:{department_id}"))
    await callback.answer("Faollashtirildi")


@router.callback_query(F.data.startswith("ct:ddel:"))
async def department_delete_ask(callback: types.CallbackQuery):
    department_id = _arg(callback.data)
    row = await db.get_department(department_id)
    if row is None:
        await callback.answer("Bo'lim topilmadi.", show_alert=True)
        return
    await _edit(
        callback.message,
        f"🗑 <b>{escape(row[1])}</b> bo'limi butunlay o'chirilsinmi?\n<i>Bu amalni qaytarib bo'lmaydi.</i>",
        _kb([[_b("🗑 Ha, o'chirish", f"ct:ddelok:{department_id}"), _b("❌ Yo'q", f"ct:dep:{department_id}")]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ct:ddelok:"))
async def department_delete(callback: types.CallbackQuery):
    department_id = _arg(callback.data)
    if not await db.delete_department(department_id):
        await callback.answer(
            "O'chirib bo'lmaydi: bo'limda xodimlar bor yoki bo'lgan. Faolsizlantirish mumkin.", show_alert=True
        )
        await _edit(callback.message, *await _screen_for(f"ct:dep:{department_id}"))
        return
    await _edit(callback.message, *await departments_screen())
    await callback.answer("O'chirildi")


# ===========================
# Wizards: entry points
# ===========================
async def _start_wizard(state: FSMContext, draft: dict):
    await state.clear()
    await state.update_data(draft=draft)


async def _ask_name(bot: Bot, state: FSMContext, draft: dict, **where):
    await state.set_state(CatalogState.entering_name)
    await state.update_data(draft={**draft, "step": "name"})
    what = "filial" if draft["kind"] == "loc" else "bo'lim"
    if draft["action"] == "new":
        text = f"➕ <b>Yangi {what}</b>\n\n{what.capitalize()} nomini yozib yuboring:"
    else:
        text = f"✏️ <b>{escape(draft['name'])}</b>\n\nYangi nomni yozib yuboring:"
    await _show(bot, state, text, _kb([CANCEL_ROW]), **where)


async def _ask_point(bot: Bot, state: FSMContext, draft: dict, **where):
    await state.set_state(CatalogState.entering_point)
    await state.update_data(draft={**draft, "step": "point"})
    text = (
        f"🌐 <b>{escape(draft['name'])}</b> joylashuvini yuboring — quyidagilardan biri:\n\n"
        "• Telegram lokatsiyasi: 📎 → Location → xaritadan nuqtani tanlang\n"
        "• Google yoki Yandex xarita havolasi (brauzerdan yoki «Ulashish» tugmasidan)\n"
        "• Koordinata matni, masalan: <code>38.829001, 65.793878</code>"
    )
    await _show(bot, state, text, _kb([CANCEL_ROW]), **where)


async def _ask_group(bot: Bot, state: FSMContext, draft: dict, **where):
    await state.set_state(CatalogState.browsing)
    await state.update_data(draft={**draft, "step": "group"})
    await _refresh_group_titles(bot)

    rows = []
    for chat_id, title, _ in await db.list_groups():
        mark = "✅ " if draft.get("group_id") == chat_id else ""
        rows.append([_b(mark + (title or f"ID {chat_id}"), f"ct:g:{chat_id}")])
    rows += [
        [_b("🚫 Guruhsiz", "ct:g:0"), _b("🔄 Yangilash", "ct:grefresh")],
        CANCEL_ROW,
    ]
    text = (
        f"👥 <b>{escape(draft['name'])}</b>: video va xabarlar qaysi guruhga yuborilsin?\n\n"
        "<i>Kerakli guruh ro'yxatda yo'qmi? Botni o'sha guruhga qo'shing, "
        "keyin «🔄 Yangilash»ni bosing.</i>"
    )
    await _show(bot, state, text, _kb(rows), **where)


async def _checklist_items(draft: dict) -> list:
    """(id, name, is_active) to choose from: active ones + currently linked inactive ones."""
    if draft["kind"] == "loc":  # a location chooses its departments
        items = await db.list_departments(active_only=False)
    else:  # a department chooses its locations
        items = [(row[0], row[1], row[5]) for row in await db.list_locations(active_only=False)]
    linked = set(draft.get("checked", []))
    return [item for item in items if item[2] or item[0] in linked]


async def _ask_links(bot: Bot, state: FSMContext, draft: dict, **where):
    await state.set_state(CatalogState.browsing)
    await state.update_data(draft={**draft, "step": "links"})
    checked = set(draft.get("checked", []))

    rows = [
        [_b(("✅ " if item_id in checked else "⬜ ") + name + ("" if is_active else " 🚫"), f"ct:chk:{item_id}")]
        for item_id, name, is_active in await _checklist_items(draft)
    ]
    rows += [[_b("💾 Saqlash", "ct:chkok")], CANCEL_ROW]
    if draft["kind"] == "loc":
        text = (
            f"🏢 <b>{escape(draft['name'])}</b> qaysi bo'lim(lar)ga tegishli? Belgilang.\n\n"
            "<i>Hech biri belgilanmasa, filial saqlanadi, lekin xodimlarga ko'rinmaydi.</i>"
        )
    else:
        text = (
            f"📍 <b>{escape(draft['name'])}</b> qaysi filial(lar)da ishlaydi? Belgilang.\n\n"
            "<i>Bitta filial — xodimga avtomatik tanlanadi. Bir nechta — xodim o'zi tanlaydi.</i>"
        )
    await _show(bot, state, text, _kb(rows), **where)


@router.callback_query(F.data.in_({"ct:newloc", "ct:newdep"}))
async def wizard_new(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    kind = "loc" if callback.data == "ct:newloc" else "dep"
    draft = {"kind": kind, "action": "new", "back": "ct:locs" if kind == "loc" else "ct:deps"}
    await _start_wizard(state, draft)
    await _ask_name(bot, state, draft, source=callback.message)
    await callback.answer()


@router.callback_query(
    F.data.startswith("ct:lname:") | F.data.startswith("ct:lpoint:") | F.data.startswith("ct:lgroup:")
    | F.data.startswith("ct:ldeps:")
)
async def wizard_edit_location(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    location_id = _arg(callback.data)
    row = await db.get_location(location_id)
    if row is None:
        await callback.answer("Filial topilmadi.", show_alert=True)
        return
    _, name, lat, lon, group_id, _ = row
    action = callback.data.split(":")[1][1:]  # lname -> name, lpoint -> point, ...
    draft = {
        "kind": "loc", "action": action, "id": location_id, "name": name,
        "old_lat": lat, "old_lon": lon, "group_id": group_id, "back": f"ct:loc:{location_id}",
    }
    await _start_wizard(state, draft)
    if action == "name":
        await _ask_name(bot, state, draft, source=callback.message)
    elif action == "point":
        await _ask_point(bot, state, draft, source=callback.message)
    elif action == "group":
        await _ask_group(bot, state, draft, source=callback.message)
    else:  # deps
        draft["checked"] = [d[0] for d in await db.get_location_departments(location_id)]
        await _ask_links(bot, state, draft, source=callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("ct:dname:") | F.data.startswith("ct:dlocs:"))
async def wizard_edit_department(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    department_id = _arg(callback.data)
    row = await db.get_department(department_id)
    if row is None:
        await callback.answer("Bo'lim topilmadi.", show_alert=True)
        return
    action = "name" if callback.data.startswith("ct:dname:") else "locs"
    draft = {"kind": "dep", "action": action, "id": department_id, "name": row[1], "back": f"ct:dep:{department_id}"}
    await _start_wizard(state, draft)
    if action == "name":
        await _ask_name(bot, state, draft, source=callback.message)
    else:
        draft["checked"] = [loc[0] for loc in await db.get_department_locations(department_id, active_only=False)]
        await _ask_links(bot, state, draft, source=callback.message)
    await callback.answer()


# ===========================
# Wizards: steps
# ===========================
async def _finish(bot: Bot, state: FSMContext, target: str, *, source=None, chat_id=None):
    """End the wizard and show the given screen (a card or a list)."""
    text, markup = await _screen_for(target)
    await _show(bot, state, text, markup, source=source, chat_id=chat_id)
    await state.clear()


@router.message(StateFilter(CatalogState.entering_name), F.text, ~F.text.startswith("/"))
async def on_name(message: types.Message, state: FSMContext, bot: Bot):
    draft = (await state.get_data()).get("draft")
    if not draft or draft.get("step") != "name":
        await state.clear()
        return

    name = _clean_name(message.text)
    error = await _name_error(draft["kind"], name, draft.get("id"))
    if error:
        await message.answer(error)
        return

    if draft["action"] == "name":  # rename
        if draft["kind"] == "loc":
            await db.rename_location(draft["id"], name)
        else:
            await db.rename_department(draft["id"], name)
        await _finish(bot, state, draft["back"], chat_id=message.chat.id)
        return

    draft["name"] = name
    if draft["kind"] == "loc":
        await _ask_point(bot, state, draft, chat_id=message.chat.id)
    else:
        draft["checked"] = []
        await _ask_links(bot, state, draft, chat_id=message.chat.id)


@router.message(StateFilter(CatalogState.entering_name))
async def on_name_wrong_input(message: types.Message):
    await message.answer("⚠️ Nomni matn ko'rinishida yozing yoki «❌ Bekor qilish»ni bosing.")


@router.message(StateFilter(CatalogState.entering_point), F.location | (F.text & ~F.text.startswith("/")))
async def on_point(message: types.Message, state: FSMContext, bot: Bot):
    draft = (await state.get_data()).get("draft")
    if not draft or draft.get("step") != "point":
        await state.clear()
        return

    if message.location:
        coordinates = (message.location.latitude, message.location.longitude)
    else:
        await bot.send_chat_action(message.chat.id, "find_location")
        status, coordinates = await geo_parse.extract_coordinates(message.text)
        if status == geo_parse.NOT_ALLOWED:
            await message.answer("⚠️ Faqat Google yoki Yandex xarita havolalari qabul qilinadi.")
            return
        if status == geo_parse.NETWORK_ERROR:
            await message.answer(
                "⚠️ Havolani ochib bo'lmadi (tarmoq xatosi). Qayta urinib ko'ring yoki "
                "koordinatani matn ko'rinishida yuboring."
            )
            return
        if status != geo_parse.OK:
            await message.answer(
                "⚠️ Koordinatani aniqlab bo'lmadi.\n\n"
                "Mobil ilovalarning «Ulashish» havolalarida koordinata bo'lmaydi. Buning o'rniga:\n"
                "• brauzerdagi xarita havolasini yuboring, yoki\n"
                "• xaritada nuqtani bosib turing, chiqqan koordinatani nusxalab yuboring "
                "(masalan <code>38.829001, 65.793878</code>), yoki\n"
                "• Telegram lokatsiyasini yuboring (📎 → Location).",
                parse_mode="HTML",
            )
            return

    lat, lon = round(coordinates[0], 7), round(coordinates[1], 7)
    await _delete_pin(bot, draft)  # a preview of an earlier attempt, if any

    # Show the point on a map so the admin can check it before it is saved;
    # the preview is deleted as soon as the admin answers
    pin = await bot.send_location(message.chat.id, latitude=lat, longitude=lon)
    draft.update(lat=lat, lon=lon, step="point_confirm", pin_message_id=pin.message_id, pin_chat_id=pin.chat.id)
    await state.set_state(CatalogState.browsing)
    await state.update_data(draft=draft)
    text = (
        f"🌐 <b>{escape(draft['name'])}</b>\nNuqta yuqoridagi xaritada: <code>{lat}, {lon}</code>\n"
        f"🗺 Yandex'da tekshirish: {_map_link(lat, lon)}"
    )
    if draft.get("old_lat") is not None:
        moved = gps.distance_to_branch((lat, lon), {"lat": draft["old_lat"], "lon": draft["old_lon"]})
        text += f"\n📏 Eski joydan {int(moved)} metr uzoqda."
    text += "\n\nTo'g'rimi?"
    await _show(bot, state, text, _kb([
        [_b("✅ To'g'ri", "ct:ptok"), _b("✏️ Qayta yuborish", "ct:ptredo")],
        CANCEL_ROW,
    ]), chat_id=message.chat.id)


@router.message(StateFilter(CatalogState.entering_point))
async def on_point_wrong_input(message: types.Message):
    await message.answer(
        "⚠️ Lokatsiya, xarita havolasi yoki koordinata yuboring, yoki «❌ Bekor qilish»ni bosing."
    )


async def _draft_for(callback: types.CallbackQuery, state: FSMContext, step: str) -> dict | None:
    """The wizard draft when it is at the expected step; otherwise answers the button and returns None."""
    draft = (await state.get_data()).get("draft")
    if not draft:
        await callback.answer(STALE_TEXT, show_alert=True)
        return None
    if draft.get("step") != step:
        await callback.answer()  # a double tap of a finished step
        return None
    return draft


@router.callback_query(F.data == "ct:ptok")
async def on_point_ok(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    draft = await _draft_for(callback, state, "point_confirm")
    if draft is None:
        return
    await _delete_pin(bot, draft)
    if draft["action"] == "point":
        await db.update_location_coordinates(draft["id"], draft["lat"], draft["lon"])
        await _finish(bot, state, draft["back"], source=callback.message)
    else:  # a new location continues with its group
        await _ask_group(bot, state, draft, source=callback.message)
    await callback.answer()


@router.callback_query(F.data == "ct:ptredo")
async def on_point_redo(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    draft = await _draft_for(callback, state, "point_confirm")
    if draft is None:
        return
    await _delete_pin(bot, draft)
    await _ask_point(bot, state, draft, source=callback.message)
    await callback.answer()


@router.callback_query(F.data == "ct:grefresh")
async def on_group_refresh(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    draft = await _draft_for(callback, state, "group")
    if draft is None:
        return
    await _ask_group(bot, state, draft, source=callback.message)
    await callback.answer("Yangilandi")


@router.callback_query(F.data.startswith("ct:g:"))
async def on_group(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    draft = await _draft_for(callback, state, "group")
    if draft is None:
        return
    group_id = _arg(callback.data)
    if group_id is None or (group_id != 0 and group_id not in {g[0] for g in await db.list_groups()}):
        await callback.answer("Bu guruh ro'yxatda yo'q. «🔄 Yangilash»ni bosing.", show_alert=True)
        return
    group_id = group_id or None  # 0 = no group

    if draft["action"] == "group":
        await db.update_location_group(draft["id"], group_id)
        await _finish(bot, state, draft["back"], source=callback.message)
    else:  # a new location continues with its departments
        draft.update(group_id=group_id, checked=[])
        await _ask_links(bot, state, draft, source=callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("ct:chk:"))
async def on_check_toggle(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    draft = await _draft_for(callback, state, "links")
    if draft is None:
        return
    item_id = _arg(callback.data)
    checked = set(draft.get("checked", []))
    checked ^= {item_id}
    draft["checked"] = sorted(checked)
    await _ask_links(bot, state, draft, source=callback.message)
    await callback.answer()


@router.callback_query(F.data == "ct:chkok")
async def on_check_save(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    draft = await _draft_for(callback, state, "links")
    if draft is None:
        return
    valid_ids = {item[0] for item in await _checklist_items(draft)}
    checked = [item_id for item_id in draft.get("checked", []) if item_id in valid_ids]

    if draft["kind"] == "loc" and draft["action"] == "new":
        if await db.catalog_name_taken("locations", draft["name"]):
            await callback.answer("Bu nom band bo'lib qoldi. Qaytadan boshlang.", show_alert=True)
            return
        location_id = await db.create_location(draft["name"], draft["lat"], draft["lon"], draft.get("group_id"), checked)
        await _finish(bot, state, f"ct:loc:{location_id}", source=callback.message)
        await callback.answer("Filial yaratildi")
        return

    if draft["kind"] == "dep" and draft["action"] == "new":
        if await db.catalog_name_taken("departments", draft["name"]):
            await callback.answer("Bu nom band bo'lib qoldi. Qaytadan boshlang.", show_alert=True)
            return
        department_id = await db.create_department(draft["name"], checked)
        await _finish(bot, state, f"ct:dep:{department_id}", source=callback.message)
        await callback.answer("Bo'lim yaratildi")
        return

    if draft["kind"] == "loc":  # edit the departments of a location
        blocked = await _departments_losing_last_location(draft["id"], keep_ids=set(checked))
        if blocked:
            await callback.answer(
                "Saqlab bo'lmaydi: bu — quyidagi bo'lim(lar)ning yagona filiali, ularda faol "
                f"xodimlar bor: {', '.join(blocked)}.",
                show_alert=True,
            )
            return
        await db.set_location_departments(draft["id"], checked)
    else:  # edit the locations of a department
        active_employees, _ = await db.department_usage(draft["id"])
        active_location_ids = {row[0] for row in await db.list_locations()}
        if active_employees and not active_location_ids & set(checked):
            await callback.answer(
                f"Kamida bitta faol filial tanlang: bo'limda {active_employees} ta faol xodim bor.",
                show_alert=True,
            )
            return
        await db.set_department_locations(draft["id"], checked)

    await _finish(bot, state, draft["back"], source=callback.message)
    await callback.answer("Saqlandi")


@router.callback_query(F.data == "ct:cancel")
async def on_cancel(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    draft = (await state.get_data()).get("draft") or {}
    await _delete_pin(bot, draft)
    await _finish(bot, state, draft.get("back", "ct:home"), source=callback.message)
    await callback.answer("Bekor qilindi")


# Must stay LAST: buttons of this module that no handler above accepted
@router.callback_query(F.data.startswith("ct:"))
async def on_stale_button(callback: types.CallbackQuery):
    await callback.answer(STALE_TEXT, show_alert=True)