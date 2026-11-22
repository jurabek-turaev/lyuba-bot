"""
Background work of the hiring flow (started from bot.py).

- Retention: personal data of candidates who were not hired is deleted after
  APPLICATION_RETENTION_DAYS; unfinished forms after DRAFT_RETENTION_DAYS.
"""
import asyncio
import logging

from aiogram import Bot

import database as db

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 3600  # once an hour; the purge itself is cheap and idempotent
APPLICATION_RETENTION_DAYS = 365  # rejected / no-show / cancelled applications
DRAFT_RETENTION_DAYS = 30  # forms the candidate started but never sent


async def purge_expired_data():
    applications, drafts = await db.purge_expired_applications(
        APPLICATION_RETENTION_DAYS, DRAFT_RETENTION_DAYS
    )
    if applications or drafts:
        logger.info("[hr] deleted %s old applications and %s old drafts", applications, drafts)


async def hr_tasks_loop(bot: Bot):
    while True:
        try:
            await purge_expired_data()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[hr] background task failed")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
