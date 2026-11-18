import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from config import OWNER_ID
import database as db

logger = logging.getLogger(__name__)


class AccessMiddleware(BaseMiddleware):
    """
    Puts the access level of the sender into the handler data as data["access"]:

    - "owner":      OWNER_ID, never restricted
    - "approved":   active employee who completed registration
    - "unapproved": everybody else (strangers, pending applicants, removed employees)

    Nothing is dropped here. The application router reacts to "unapproved" users and
    swallows everything it does not handle, so the employee handlers never see them.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        chat = data.get("event_chat")

        # Private chats only: groups are not touched
        if user is not None and (chat is None or chat.type == "private"):
            if user.id == OWNER_ID:
                data["access"] = "owner"
            elif await db.is_employee_approved(user.id):
                data["access"] = "approved"
            else:
                data["access"] = "unapproved"

        return await handler(event, data)