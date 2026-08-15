import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from config import BOT_TOKEN
from database import init_db
from handlers import router as main_router
from services.auto_close import auto_close_loop
from services.monthly_report import monthly_report_loop

logger = logging.getLogger(__name__)

# VPS (OVH) ↔ Telegram ulanishi ba'zan bir necha soniyaga uziladi. Sukut
# bo'yicha timeout'ni biroz oshiramiz — shunda qisqa uzilishlarda so'rov darhol
# yiqilmay, ulanish tiklanguncha kutib turadi. Bu ayniqsa xodimga javob berish
# (message.answer) uchun muhim: aks holda bot "javob bermayotgan" ko'rinadi.
_SESSION_TIMEOUT = 60


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    # aiogram'ning juda "gapiruvchan" loglarini biroz jimlashtiramiz
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


async def main():
    setup_logging()

    # Baza jadvallarini yaratish (agar mavjud bo'lmasa)
    await init_db()

    session = AiohttpSession(timeout=_SESSION_TIMEOUT)
    bot = Bot(token=BOT_TOKEN, session=session)
    dp = Dispatcher()
    dp.include_router(main_router)

    # Fon vazifalari: smenani avtomatik yopish + oylik tabel/arxivlash
    background_tasks = [
        asyncio.create_task(auto_close_loop(bot)),
        asyncio.create_task(monthly_report_loop(bot)),
    ]

    # Eski (ishlatilmagan) update'larni tashlab, botni ishga tushirish
    await bot.delete_webhook(drop_pending_updates=True)

    logger.info("Bot ishga tushdi.")
    try:
        await dp.start_polling(bot)
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await bot.session.close()
        logger.info("Bot to'xtatildi.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
