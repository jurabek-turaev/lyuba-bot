import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import BOT_TOKEN
from database import init_db
from handlers import router as main_router
from services.auto_close import auto_close_loop
from services.monthly_report import monthly_report_loop

logger = logging.getLogger(__name__)


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

    bot = Bot(token=BOT_TOKEN)
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
