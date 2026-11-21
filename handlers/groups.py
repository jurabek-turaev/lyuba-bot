"""
Remembers the Telegram groups the bot is in (table tg_groups), so the admin picks
a location's group from a list instead of typing its id.

- the bot is added to / removed from a group      -> my_chat_member update
- a group is renamed                                -> new_chat_title service message
- a group becomes a supergroup (its id changes)     -> migrate_to_chat_id service message;
                                                       locations follow the new id
Service messages reach the bot even with privacy mode on.
"""
import logging

from aiogram import F, Router, types

import database as db

logger = logging.getLogger(__name__)

router = Router()
GROUP_CHATS = {"group", "supergroup"}
router.my_chat_member.filter(F.chat.type.in_(GROUP_CHATS))
router.message.filter(F.chat.type.in_(GROUP_CHATS))

MEMBER_STATUSES = {"member", "administrator", "creator"}


@router.my_chat_member()
async def bot_membership_changed(event: types.ChatMemberUpdated):
    new = event.new_chat_member
    is_member = new.status in MEMBER_STATUSES or (new.status == "restricted" and getattr(new, "is_member", False))
    await db.upsert_group(event.chat.id, event.chat.title, is_member)
    logger.info("[groups] %s %s (%s)", "joined" if is_member else "left", event.chat.title, event.chat.id)


@router.message(F.new_chat_title)
async def group_renamed(message: types.Message):
    await db.upsert_group(message.chat.id, message.new_chat_title, True)


@router.message(F.migrate_to_chat_id)
async def group_migrated(message: types.Message):
    await db.migrate_group(message.chat.id, message.migrate_to_chat_id)
    logger.info("[groups] %s migrated to %s", message.chat.id, message.migrate_to_chat_id)