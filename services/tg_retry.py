"""
Telegram API chaqiruvlarini tarmoq beqarorligiga qarshi himoyalash.

VPS (OVH) ↔ Telegram serverlari orasidagi ulanish ba'zan bir necha soniyaga
uziladi (Request timeout / Connection reset). Bunday vaqtinchalik uzilishlarda
xabar yubormay qolmaslik uchun har bir chaqiruvni bir necha marta qayta uramiz.
Faqat TARMOQ xatolari (TelegramNetworkError) qayta urinishga arziydi — mantiqiy
xatolar (masalan bot guruhdan chiqarilgan) darhol ko'tariladi.
"""
import asyncio
import logging

from aiogram.exceptions import TelegramNetworkError

logger = logging.getLogger(__name__)

DEFAULT_ATTEMPTS = 3
DEFAULT_DELAY_SECONDS = 2.0


async def with_retry(coro_factory, *, attempts: int = DEFAULT_ATTEMPTS,
                     delay: float = DEFAULT_DELAY_SECONDS, what: str = "so'rov"):
    """
    coro_factory — har urinishda YANGI awaitable qaytaruvchi funksiya
    (masalan: lambda: bot.send_message(...)). Coroutine'ni bir marta yaratib
    qayta await qilib bo'lmagani uchun har safar yangisini yaratamiz.

    Barcha urinishlar tarmoq xatosi bilan tugasa — oxirgi xato ko'tariladi.
    """
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return await coro_factory()
        except TelegramNetworkError as e:
            last_error = e
            if attempt < attempts:
                logger.warning(
                    "%s tarmoq xatosi (urinish %d/%d), %.1f s dan keyin qayta urinamiz",
                    what, attempt, attempts, delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.error("%s barcha %d urinishda muvaffaqiyatsiz", what, attempts)
    raise last_error
