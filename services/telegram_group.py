from aiogram import Bot

from config import GROUP_ID, WORK_LOCATIONS
from services.tg_retry import with_retry

# Guruhga yuborishda Telegram API ba'zan tarmoq muammosi sababli uzoq osilib
# qoladi. Aniq (qisqa) timeout berib, uzun osilishlarning oldini olamiz —
# timeout bo'lsa with_retry qayta urinadi.
_REQUEST_TIMEOUT = 15


def _group_id_for_branch(branch: str) -> int:
    """
    Agar WORK_LOCATIONS'da shu filial uchun alohida 'group_id' ko'rsatilgan
    bo'lsa — o'sha ishlatiladi (masalan 'Ishlab chiqarish' uchun).
    Ko'rsatilmagan bo'lsa — umumiy GROUP_ID ishlatiladi (savdo filiallari
    uchun bo'lgani kabi).
    """
    branch_info = WORK_LOCATIONS.get(branch, {})
    return branch_info.get("group_id", GROUP_ID)


async def send_video(bot: Bot, branch: str, video_id: str):
    """Dumaloq video kelgan zahoti, shu filialga tegishli guruhga yuboriladi."""
    group_id = _group_id_for_branch(branch)
    await with_retry(
        lambda: bot.send_video_note(
            chat_id=group_id, video_note=video_id, request_timeout=_REQUEST_TIMEOUT
        ),
        what="Guruhga video",
    )


async def send_check_in(bot: Bot, name: str, branch: str, distance: int, time_str: str):
    group_id = _group_id_for_branch(branch)
    text = (
        f"👤 Xodim: {name}\n"
        f"🏢 Filial: {branch}\n"
        f"📍 Holat: Keldi\n"
        f"📏 Masofa: {distance} metr\n"
        f"🕒 Vaqt: {time_str}"
    )
    await with_retry(
        lambda: bot.send_message(
            chat_id=group_id, text=text, request_timeout=_REQUEST_TIMEOUT
        ),
        what="Guruhga 'Keldi'",
    )


async def send_check_out(bot: Bot, name: str, branch: str, distance: int, time_str: str, hours: float):
    group_id = _group_id_for_branch(branch)
    text = (
        f"👤 Xodim: {name}\n"
        f"🏢 Filial: {branch}\n"
        f"📍 Holat: Ketdi\n"
        f"📏 Masofa: {distance} metr\n"
        f"🕒 Vaqt: {time_str}\n"
    )
    await with_retry(
        lambda: bot.send_message(
            chat_id=group_id, text=text, request_timeout=_REQUEST_TIMEOUT
        ),
        what="Guruhga 'Ketdi'",
    )


async def send_auto_close(bot: Bot, name: str, branch: str, time_str: str, hours: float):
    group_id = _group_id_for_branch(branch)
    text = (
        f"⚠️ Avtomatik yopildi\n"
        f"👤 Xodim: {name}\n"
        f"🏢 Filial: {branch}\n"
        f"📍 Holat: Ketdim bosilmadi — maksimal smena vaqti tugagani uchun tizim tomonidan yopildi\n"
        f"🕒 Yopilgan vaqt: {time_str}\n"
    )
    await with_retry(
        lambda: bot.send_message(
            chat_id=group_id, text=text, request_timeout=_REQUEST_TIMEOUT
        ),
        what="Guruhga 'Avtomatik yopildi'",
    )