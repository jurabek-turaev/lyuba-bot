import asyncio
import logging
import os
from datetime import datetime

from aiogram import Bot
from aiogram.types import FSInputFile

from config import OWNER_ID
import database as db
from services import excel

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 300  # har 5 daqiqada tekshiradi
RUN_HOUR = 0
RUN_MINUTE = 30

# Bir oy uchun faqat bir marta ishlaganini eslab qolish — restart'ga chidamli.
# Ichida oxirgi bajarilgan oy yoziladi, masalan "2026-07".
STATE_FILE = "data/.last_monthly_report"


def _prev_month(today: datetime):
    """Berilgan sanadan oldingi oyni (year, month) qaytaradi."""
    year = today.year
    month = today.month - 1
    if month == 0:
        month = 12
        year -= 1
    return year, month


def _read_last_done() -> str:
    if not os.path.exists(STATE_FILE):
        return ""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        logger.exception("[monthly] holat faylini o'qib bo'lmadi")
        return ""


def _write_last_done(tag: str):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write(tag)
    except Exception:
        logger.exception("[monthly] holat faylini yozib bo'lmadi")


async def monthly_report_loop(bot: Bot):
    """
    bot.py da fon vazifasi sifatida ishga tushiriladi.

    Har oyning 1-sanasi soat 00:30 (yoki undan keyin, agar bot o'sha paytda
    o'chiq bo'lsa — keyingi ishga tushishida) OWNER_IDga o'tgan oy tabelini
    Excel qilib yuboradi va o'sha oyni arxivlaydi. Har oy uchun faqat bir marta.
    """
    while True:
        try:
            await _maybe_run(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[monthly] oylik hisobotda kutilmagan xatolik")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def _maybe_run(bot: Bot):
    now = datetime.now()

    # Faqat oyning 1-sanasi, belgilangan vaqtdan keyin ishlaydi
    if now.day != 1:
        return
    if (now.hour, now.minute) < (RUN_HOUR, RUN_MINUTE):
        return

    year, month = _prev_month(now)
    tag = f"{year:04d}-{month:02d}"

    # Shu oy allaqachon bajarilganmi?
    if _read_last_done() == tag:
        return

    logger.info("[monthly] %s oyi uchun tabel tayyorlanmoqda va arxivlanmoqda", tag)

    # 1) Excel tabel yasab, egasiga yuborish
    try:
        file_path = await excel.generate_monthly_tabel(year, month)
        await bot.send_document(
            chat_id=OWNER_ID,
            document=FSInputFile(file_path),
            caption=f"📊 {month:02d}.{year} oyi uchun avtomatik tabel (oy yakunlandi)",
        )
    except Exception:
        logger.exception("[monthly] %s tabelini yuborib bo'lmadi — arxivlash TO'XTATILDI", tag)
        # Excel yuborilmasa arxivlamaymiz — keyingi tekshiruvda qayta uriniladi
        return

    # 2) O'sha oyni arxivlash (o'chirilmaydi, faqat archived=1)
    try:
        archived_count = await db.archive_month(year, month)
        logger.info("[monthly] %s oyi arxivlandi (%s yozuv)", tag, archived_count)
    except Exception:
        logger.exception("[monthly] %s oyini arxivlab bo'lmadi", tag)
        # Tabel yuborilgan, lekin arxivlash muvaffaqiyatsiz — holatni belgilamaymiz,
        # keyingi tekshiruvda qayta uriniladi (tabel qayta yuborilishi mumkin, bu OK)
        return

    # 3) Muvaffaqiyat — bu oyni "bajarildi" deb belgilaymiz
    _write_last_done(tag)
