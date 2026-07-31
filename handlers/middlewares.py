import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from config import OWNER_ID
import database as db

logger = logging.getLogger(__name__)


class ActiveEmployeeMiddleware(BaseMiddleware):
    """
    Ishdan ketgan (is_active=0) xodimlarni to'sadi: ular botga yozsa,
    hech qanday javob bermaydi va handlerga o'tkazilmaydi — bot ular
    uchun butunlay "jim" bo'ladi.

    OWNER_ID hech qachon bloklanmaydi.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")

        # Faqat shaxsiy chatdagi xabarlar uchun — guruhga tegmaymiz
        chat = data.get("event_chat")
        if chat is not None and chat.type != "private":
            return await handler(event, data)

        if user is not None and user.id != OWNER_ID:
            if not await db.is_employee_active(user.id):
                logger.info("Bloklangan xodim yozdi, e'tiborsiz qoldirildi (user_id=%s)", user.id)
                # Callback bo'lsa, "soat" aylanib qolmasligi uchun jimgina yopamiz
                if isinstance(event, CallbackQuery):
                    try:
                        await event.answer()
                    except Exception:
                        pass
                # Message yoki boshqa event — umuman javob bermaymiz
                return None

        return await handler(event, data)
