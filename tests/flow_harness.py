"""
Runs the real bot (aiogram Dispatcher + all routers + database) against a fake
Telegram connection: every request the bot makes is recorded in self.log instead
of being sent. Each test gets its own empty database in a temporary folder.
"""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery, DeleteMessage, EditMessageReplyMarkup, EditMessageText, GetChat, SendChatAction,
    SendLocation, SendMessage, SendVideoNote,
)
from aiogram.types import Chat, ChatFullInfo, Message, Update

import config
import database as db

OWNER = config.OWNER_ID
SAVDO_GROUP = int(os.environ["GROUP_ID"])
ZAVOD_GROUP = int(os.environ["PRODUCTION_GROUP_ID"])
ZAVOD_POINT = (float(os.environ["PRODUCTION_LAT"]), float(os.environ["PRODUCTION_LON"]))
SHOP_POINTS = {name: (lat, lon) for name, lat, lon in db.LEGACY_SHOPS}


def _buttons(markup):
    if markup is None:
        return []
    if hasattr(markup, "inline_keyboard"):
        return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]
    if hasattr(markup, "keyboard"):
        return [b.text for row in markup.keyboard for b in row]
    return []


class FakeSession(BaseSession):
    """Records the bot's requests and answers them like Telegram would."""

    def __init__(self, log: list, group_titles: dict):
        super().__init__()
        self.log = log
        self.group_titles = group_titles
        self._next_message_id = 1000

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            self.log.append({"type": "msg", "chat": method.chat_id, "text": method.text,
                             "buttons": _buttons(method.reply_markup)})
        elif isinstance(method, EditMessageText):
            self.log.append({"type": "edit", "text": method.text, "buttons": _buttons(method.reply_markup)})
        elif isinstance(method, SendVideoNote):
            self.log.append({"type": "video", "chat": method.chat_id})
        elif isinstance(method, SendLocation):
            self.log.append({"type": "pin", "lat": method.latitude, "lon": method.longitude})
            self._next_message_id += 1
            return Message(message_id=self._next_message_id, date=datetime.now(),
                           chat=Chat(id=method.chat_id, type="private"))
        elif isinstance(method, DeleteMessage):
            self.log.append({"type": "delete", "chat": method.chat_id, "message_id": method.message_id})
            return True
        elif isinstance(method, AnswerCallbackQuery):
            if method.text:
                self.log.append({"type": "alert", "text": method.text})
            return True
        elif isinstance(method, GetChat):
            return ChatFullInfo(id=method.chat_id, type="supergroup", accent_color_id=0, max_reaction_count=0,
                                title=self.group_titles.get(method.chat_id, f"Guruh {method.chat_id}"))
        elif isinstance(method, (EditMessageReplyMarkup, SendChatAction)):
            return True
        else:
            self.log.append({"type": type(method).__name__})
        if method.__returning__ is bool:
            return True
        return Message(message_id=500, date=datetime.now(),
                       chat=Chat(id=getattr(method, "chat_id", 0) or 0, type="private"))

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def close(self):
        pass


class FlowTestCase(unittest.IsolatedAsyncioTestCase):
    """Base class: fresh database + bot + dispatcher for every test."""

    seed_legacy = True  # migrate the old hardcoded setup (3 shops + Zavod)

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self._tmp.name, "test.db")
        if self.seed_legacy:
            await db.init_db()

        self.log = []
        self.group_titles = {SAVDO_GROUP: "Savdo guruhi", ZAVOD_GROUP: "Zavod guruhi"}
        self.bot = Bot("123456:TEST", session=FakeSession(self.log, self.group_titles))
        self._update_id = 0
        self.restart()

    async def asyncTearDown(self):
        await asyncio.sleep(0.01)  # let background tasks of the last update finish
        db.DB_PATH = self._old_db_path
        self._tmp.cleanup()

    def restart(self):
        """A new Dispatcher = empty FSM memory, exactly like a bot restart."""
        from handlers import router as main_router
        main_router._parent_router = None
        self.dp = Dispatcher()
        self.dp.include_router(main_router)

    # ---------- sending updates ----------
    def _next_id(self) -> int:
        self._update_id += 1
        return self._update_id

    @staticmethod
    def _user(user_id: int) -> dict:
        return {"id": user_id, "is_bot": False, "first_name": "Test"}

    async def feed(self, update: dict) -> list:
        """Feed one update; returns what the bot sent because of it."""
        start = len(self.log)
        await self.dp.feed_update(self.bot, Update.model_validate(update))
        await asyncio.sleep(0.01)  # background tasks (video forwarding)
        return self.log[start:]

    async def send(self, user_id: int, **fields) -> list:
        update_id = self._next_id()
        message = {"message_id": update_id, "date": int(datetime.now().timestamp()),
                   "chat": {"id": user_id, "type": "private"}, "from": self._user(user_id)}
        text = fields.get("text")
        if text and text.startswith("/"):
            fields["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        message.update(fields)
        return await self.feed({"update_id": update_id, "message": message})

    async def press(self, user_id: int, data: str) -> list:
        update_id = self._next_id()
        message = {"message_id": 500, "date": int(datetime.now().timestamp()),
                   "chat": {"id": user_id, "type": "private"}, "from": self._user(user_id), "text": "x"}
        return await self.feed({"update_id": update_id, "callback_query": {
            "id": str(update_id), "from": self._user(user_id), "chat_instance": "x",
            "data": data, "message": message}})

    async def group_event(self, chat_id: int, **fields) -> list:
        update_id = self._next_id()
        message = {"message_id": update_id, "date": int(datetime.now().timestamp()),
                   "chat": {"id": chat_id, "type": "group", "title": "G"}, "from": self._user(1)}
        message.update(fields)
        return await self.feed({"update_id": update_id, "message": message})

    async def bot_added_to_group(self, chat_id: int, title: str) -> list:
        update_id = self._next_id()
        me = {"id": 123456, "is_bot": True, "first_name": "Bot"}
        return await self.feed({"update_id": update_id, "my_chat_member": {
            "chat": {"id": chat_id, "type": "supergroup", "title": title}, "from": self._user(1),
            "date": int(datetime.now().timestamp()),
            "old_chat_member": {"status": "left", "user": me},
            "new_chat_member": {"status": "member", "user": me}}})

    # ---------- common employee actions ----------
    VIDEO = {"video_note": {"file_id": "video", "file_unique_id": "u", "length": 1, "duration": 1}}

    @staticmethod
    def live(point) -> dict:
        return {"location": {"latitude": point[0], "longitude": point[1], "live_period": 900}}

    async def make_employee(self, user_id: int, name: str, department: str | None):
        """An approved employee, optionally with a department."""
        await db.save_employee_name(user_id, name, None)
        if department:
            await db.set_employee_department(user_id, (await db.get_department_by_name(department))[0])

    async def check_in(self, user_id: int, point) -> list:
        await self.send(user_id, **self.VIDEO)
        await self.send(user_id, text="📍 Keldim")
        return await self.send(user_id, **self.live(point))

    async def check_out(self, user_id: int, point) -> list:
        await self.send(user_id, **self.VIDEO)
        await self.send(user_id, text="📍 Ketdim")
        return await self.send(user_id, **self.live(point))

    # ---------- lookups ----------
    @staticmethod
    async def location_id(name: str) -> int:
        for row in await db.list_locations(active_only=False):
            if row[1] == name:
                return row[0]
        raise AssertionError(f"no location {name!r}")

    @staticmethod
    async def department_id(name: str) -> int:
        return (await db.get_department_by_name(name))[0]

    async def query(self, sql: str, *params):
        import aiosqlite
        async with aiosqlite.connect(db.DB_PATH) as conn:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            await conn.commit()  # the tests also use it for UPDATE / INSERT
            return rows

    # ---------- assertions ----------
    @staticmethod
    def _dump(entries) -> str:
        return "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)

    def assertSent(self, entries, *needles):
        dump = self._dump(entries)
        for needle in needles:
            self.assertIn(needle, dump, f"{needle!r} not found in:\n{dump}")

    def assertNotSent(self, entries, *needles):
        dump = self._dump(entries)
        for needle in needles:
            self.assertNotIn(needle, dump, f"{needle!r} unexpectedly found in:\n{dump}")

    def sent_to(self, entries, chat_id: int, kind: str = "msg") -> list:
        return [e for e in entries if e.get("type") == kind and e.get("chat") == chat_id]
