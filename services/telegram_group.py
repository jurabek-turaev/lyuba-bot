import logging

from aiogram import Bot

from services.tg_retry import with_retry

logger = logging.getLogger(__name__)

# Guruhga yuborishda Telegram API ba'zan tarmoq muammosi sababli uzoq osilib
# qoladi. Aniq (qisqa) timeout berib, uzun osilishlarning oldini olamiz —
# timeout bo'lsa with_retry qayta urinadi.
_REQUEST_TIMEOUT = 15

# Every function takes the group of the location (locations.group_id).
# A location without a group (None) is skipped with a warning instead of failing.


async def send_video(bot: Bot, group_id: int | None, branch: str, video_id: str):
    """Dumaloq video kelgan zahoti, shu filialga tegishli guruhga yuboriladi."""
    if group_id is None:
        logger.warning("Filialga guruh biriktirilmagan, video yuborilmadi (branch=%s)", branch)
        return
    await with_retry(
        lambda: bot.send_video_note(
            chat_id=group_id, video_note=video_id, request_timeout=_REQUEST_TIMEOUT
        ),
        what="Guruhga video",
    )


async def _send_text(bot: Bot, group_id: int | None, branch: str, text: str, what: str):
    if group_id is None:
        logger.warning("Filialga guruh biriktirilmagan, xabar yuborilmadi (%s, branch=%s)", what, branch)
        return
    await with_retry(
        lambda: bot.send_message(
            chat_id=group_id, text=text, request_timeout=_REQUEST_TIMEOUT
        ),
        what=what,
    )


async def send_check_in(bot: Bot, group_id: int | None, name: str, branch: str, distance: int, time_str: str):
    text = (
        f"👤 Xodim: {name}\n"
        f"🏢 Filial: {branch}\n"
        f"📍 Holat: Keldi\n"
        f"📏 Masofa: {distance} metr\n"
        f"🕒 Vaqt: {time_str}"
    )
    await _send_text(bot, group_id, branch, text, "Guruhga 'Keldi'")


async def send_check_out(bot: Bot, group_id: int | None, name: str, branch: str, distance: int,
                         time_str: str, hours: float):
    text = (
        f"👤 Xodim: {name}\n"
        f"🏢 Filial: {branch}\n"
        f"📍 Holat: Ketdi\n"
        f"📏 Masofa: {distance} metr\n"
        f"🕒 Vaqt: {time_str}\n"
        f"⏱ Ishlagan vaqti: {hours:g} soat"
    )
    await _send_text(bot, group_id, branch, text, "Guruhga 'Ketdi'")


async def send_auto_close(bot: Bot, group_id: int | None, name: str, branch: str, time_str: str, hours: float):
    text = (
        f"⚠️ Avtomatik yopildi\n"
        f"👤 Xodim: {name}\n"
        f"🏢 Filial: {branch}\n"
        f"📍 Holat: Ketdim bosilmadi — maksimal smena vaqti tugagani uchun tizim tomonidan yopildi\n"
        f"🕒 Yopilgan vaqt: {time_str}\n"
        f"⏱ Hisoblangan vaqt: {hours:g} soat"
    )
    await _send_text(bot, group_id, branch, text, "Guruhga 'Avtomatik yopildi'")