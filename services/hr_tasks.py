"""
Background work of the hiring flow (started from bot.py), every few minutes:

- Reminder: the candidate gets a reminder at REMINDER_HOUR the day before the
  interview. Not sent when the invitation itself came less than MIN_NOTICE before
  the interview (the candidate has just read it). If the bot was down at that
  hour, it is sent as soon as it is up again, while the interview is still ahead.
- Result: RESULT_DELAY after the interview time the admin is asked how it went,
  with the card's buttons (hire / reject / move / did not come).
- Retention: personal data of candidates who were not hired is deleted after
  APPLICATION_RETENTION_DAYS; unfinished forms after DRAFT_RETENTION_DAYS.

Every step is recorded in the database first, so nothing is sent twice.
"""
import asyncio
import logging
from datetime import datetime, time, timedelta
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

import database as db
from config import OWNER_ID
from services import hr_format, hr_messages

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 300
REMINDER_HOUR = 18
MIN_NOTICE = timedelta(hours=24)
RESULT_DELAY = timedelta(hours=1)
APPLICATION_RETENTION_DAYS = 365  # rejected / no-show / cancelled applications
DRAFT_RETENTION_DAYS = 30  # forms the candidate started but never sent

MINUTE_FORMAT = "%Y-%m-%d %H:%M"


async def purge_expired_data():
    applications, drafts = await db.purge_expired_applications(
        APPLICATION_RETENTION_DAYS, DRAFT_RETENTION_DAYS
    )
    if applications or drafts:
        logger.info("[hr] deleted %s old applications and %s old drafts", applications, drafts)


async def send_reminders(bot: Bot, now: datetime | None = None):
    now = now or datetime.now()
    upcoming = await db.get_interviews_between(
        now.strftime(MINUTE_FORMAT), (now + timedelta(days=2)).strftime(MINUTE_FORMAT)
    )
    for app in upcoming:
        if app["reminder_sent_at"]:
            continue
        interview = datetime.strptime(app["interview_at"], MINUTE_FORMAT)
        if now < datetime.combine(interview.date() - timedelta(days=1), time(REMINDER_HOUR)):
            continue
        if not await db.mark_reminder_sent(app["id"]):
            continue
        invited = datetime.strptime(app["invited_at"], "%Y-%m-%d %H:%M:%S")
        if interview - invited < MIN_NOTICE:
            continue  # the invitation itself is fresh

        lat = lon = None
        if app["interview_location_id"]:
            location = await db.get_location(app["interview_location_id"])
            if location is not None:
                lat, lon = location[2], location[3]
        text = hr_messages.reminder(app["full_name"], app["interview_at"], app["interview_place"],
                                    lat, lon, app["interview_note"], today=interview.date() == now.date())
        try:
            await bot.send_message(app["user_id"], text, parse_mode="HTML", disable_web_page_preview=True)
            if lat is not None:
                await bot.send_location(app["user_id"], lat, lon)
        except TelegramAPIError:
            logger.warning("[hr] reminder not delivered (application #%s)", app["id"])


async def ask_results(bot: Bot, now: datetime | None = None):
    from handlers.application import (
        card_keyboard,  # handlers import services, not the other way round
    )

    now = now or datetime.now()
    for app in await db.get_interviews_without_result((now - RESULT_DELAY).strftime(MINUTE_FORMAT)):
        if not await db.mark_result_asked(app["id"]):
            continue
        try:
            await bot.send_message(
                OWNER_ID,
                f"🕙 <b>Suhbat vaqti o'tdi:</b> {escape(app['full_name'])} (#{app['id']})\n"
                f"{hr_format.interview_time(app['interview_at'])} · {escape(app['interview_place'] or '—')}\n\n"
                "Natija qanday bo'ldi?",
                reply_markup=card_keyboard(app), parse_mode="HTML",
            )
        except TelegramAPIError:
            logger.warning("[hr] result question not delivered (application #%s)", app["id"])


async def hr_tasks_loop(bot: Bot):
    while True:
        for task in (send_reminders, ask_results):
            try:
                await task(bot)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[hr] %s failed", task.__name__)
        try:
            await purge_expired_data()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[hr] purge failed")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
