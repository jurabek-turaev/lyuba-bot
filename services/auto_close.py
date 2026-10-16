import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot

from config import MAX_SHIFT_HOURS
import database as db
from services import worktime, telegram_group

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60  # har daqiqada tekshiradi
CHECKOUT_GRACE_MINUTES = 15  # video (ketish niyati) yuborilgandan keyin kutiladigan vaqt


async def auto_close_loop(bot: Bot):
    """bot.py da fon vazifasi (background task) sifatida ishga tushiriladi."""
    while True:
        try:
            await _check_and_close(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[auto_close] tekshiruvda kutilmagan xatolik")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def _check_and_close(bot: Bot):
    open_records = await db.get_all_open_attendance()
    if not open_records:
        return

    now = datetime.now()

    for record in open_records:
        attendance_id, user_id, full_name, branch, work_date, check_in_time_str, checkout_requested_at_str = record

        check_in_dt = datetime.strptime(check_in_time_str, "%Y-%m-%d %H:%M:%S")

        if checkout_requested_at_str:
            # Xodim allaqachon ketish niyatini bildirgan (video yubordi),
            # lekin 'Ketdim'ni to'liq bosmagan — 15 daqiqa kutamiz.
            requested_dt = datetime.strptime(checkout_requested_at_str, "%Y-%m-%d %H:%M:%S")
            auto_close_at = requested_dt + timedelta(minutes=CHECKOUT_GRACE_MINUTES)
            # Chiqish vaqti sifatida VIDEO yuborilgan payt yoziladi —
            # shunda ortiqcha soat "yig'ilib" ketmaydi.
            check_out_time_str = checkout_requested_at_str
        else:
            # Xodim umuman ketish urinishini boshlamagan (video ham yubormagan) —
            # bu holat uchun umumiy xavfsizlik chegarasi ishlaydi.
            auto_close_at = check_in_dt + timedelta(hours=MAX_SHIFT_HOURS)
            check_out_time_str = auto_close_at.strftime("%Y-%m-%d %H:%M:%S")

        if now < auto_close_at:
            continue

        await db.auto_close_attendance(attendance_id, check_out_time_str)

        hours = worktime.calculate_hours(check_in_time_str, check_out_time_str)
        await db.save_work_hours(attendance_id, hours)

        await telegram_group.send_auto_close(bot, full_name, branch, check_out_time_str, hours)

        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "⚠️ Siz 'Ketdim' tugmasini to'liq bosmagansiz.\n"
                    f"Ish kuningiz avtomatik yakunlandi ({check_out_time_str})."
                ),
            )
        except Exception:
            # Foydalanuvchi botni bloklagan yoki xabar yubora olmasligi mumkin — davom etamiz
            logger.warning("[auto_close] %s (user_id=%s) ga xabar yuborib bo'lmadi", full_name, user_id)