"""
End-to-end tests of the whole bot: real handlers, real SQLite database (a temporary
copy per test), fake Telegram connection. No network, no real .env.

Run:  python -m unittest discover -s tests -t . -v
"""
import os
import sqlite3
from datetime import datetime, timedelta
from unittest import mock

import config
import database as db
from services import auto_close, excel
from tests.flow_harness import (
    OWNER, SAVDO_GROUP, SHOP_POINTS, ZAVOD_GROUP, ZAVOD_POINT, FlowTestCase,
)

FAR_AWAY = (41.311081, 69.240562)  # Tashkent, hundreds of km from every location
YANDEX_LINK = "https://yandex.uz/maps/org/lyuba/72333058136/?ll=65.793878%2C38.829001&z=16.89"


# =====================================================================
# 1. Migration of an existing (old) database
# =====================================================================
class MigrationTests(FlowTestCase):
    seed_legacy = False  # each test builds its own database

    def _old_database(self):
        """The schema and data as they were before departments/locations moved to the DB."""
        conn = sqlite3.connect(db.DB_PATH)
        conn.executescript("""
            CREATE TABLE employees (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT,
                                    department TEXT, is_active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE attendance (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                full_name TEXT NOT NULL, branch TEXT NOT NULL, work_date TEXT NOT NULL, check_in_time TEXT,
                check_in_distance INTEGER, check_out_time TEXT, check_out_distance INTEGER, work_hours REAL,
                auto_closed INTEGER DEFAULT 0, checkout_requested_at TEXT, archived INTEGER NOT NULL DEFAULT 0);
            INSERT INTO employees VALUES (1, 'Savdo Xodim', NULL, 'Savdo', 1);
            INSERT INTO employees VALUES (2, 'Haydovchi Xodim', NULL, 'Haydovchi', 1);
            INSERT INTO employees VALUES (3, 'Yangi Xodim', NULL, NULL, 1);
            INSERT INTO attendance (user_id, full_name, branch, work_date, check_in_time, check_out_time, work_hours)
                VALUES (1, 'Savdo Xodim', 'Eski bozor', '2026-11-01', '2026-11-01 09:00:00', '2026-11-01 18:00:00', 9);
            INSERT INTO attendance (user_id, full_name, branch, work_date, check_in_time)
                VALUES (2, 'Haydovchi Xodim', 'Haydovchi', '2026-11-02', '2026-11-02 08:00:00');
            INSERT INTO attendance (user_id, full_name, branch, work_date, check_in_time, check_out_time)
                VALUES (1, 'Savdo Xodim', 'Yopilgan joy', '2026-10-01', '2026-10-01 09:00:00', '2026-10-01 10:00:00');
        """)
        conn.commit()
        conn.close()

    async def test_old_database_is_migrated_once_without_touching_old_data(self):
        self._old_database()
        await db.init_db()
        first = await self.query("SELECT id, name, lat, lon, group_id FROM locations ORDER BY id")
        await db.init_db()  # every restart runs the migration again: nothing may change
        self.assertEqual(first, await self.query("SELECT id, name, lat, lon, group_id FROM locations ORDER BY id"))

        self.assertEqual([row[1] for row in first], ["Lyuba Orzu bozor", "Eski bozor", "Koson Lyuba", "Zavod"])
        self.assertEqual(first[3][2:], (*ZAVOD_POINT, ZAVOD_GROUP))
        self.assertTrue(all(row[4] == SAVDO_GROUP for row in first[:3]))
        self.assertEqual(len(await db.list_departments()), 4)

        employees = dict(await self.query("SELECT user_id, department_id FROM employees"))
        self.assertEqual(employees, {1: await self.department_id("Savdo"),
                                     2: await self.department_id("Haydovchi"), 3: None})
        # old text columns are untouched; rows are linked by name
        rows = await self.query("SELECT branch, location_id FROM attendance ORDER BY id")
        self.assertEqual(rows, [("Eski bozor", await self.location_id("Eski bozor")),
                                ("Haydovchi", await self.location_id("Zavod")),
                                ("Yopilgan joy", None)])

    async def test_open_shift_from_old_version_is_auto_closed_to_the_right_group(self):
        self._old_database()
        await db.init_db()
        await db.mark_checkout_requested(2)
        await self.query("UPDATE attendance SET checkout_requested_at = '2026-11-02 17:00:00' WHERE id = 2")
        await auto_close._check_and_close(self.bot)
        self.assertSent(self.sent_to(self.log, ZAVOD_GROUP), "Avtomatik yopildi", "Haydovchi Xodim")

    async def test_new_install_without_old_settings_starts_with_an_empty_catalog(self):
        with mock.patch.multiple(config, LEGACY_GROUP_ID=None, LEGACY_PRODUCTION_LAT=None,
                                 LEGACY_PRODUCTION_LON=None, LEGACY_PRODUCTION_GROUP_ID=None):
            await db.init_db()
        self.assertEqual(await db.list_locations(), [])
        self.assertEqual(await db.list_departments(), [])

        await self.make_employee(5, "Birinchi Xodim", None)
        sent = await self.send(5, text="/start")
        self.assertSent(sent, "bo'limlar sozlanmagan")

    async def test_failed_migration_changes_nothing(self):
        self._old_database()
        with mock.patch.object(db, "_link_rows_by_name", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                await db.init_db()
        tables = {row[0] for row in await self.query("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertNotIn("departments", tables)
        await db.init_db()  # the next start succeeds
        self.assertEqual(len(await db.list_departments()), 4)


# =====================================================================
# 2. Registration: application -> approval -> department -> location
# =====================================================================
class RegistrationTests(FlowTestCase):
    async def test_application_approval_and_department_choice(self):
        user = 2001
        self.assertSent(await self.send(user, text="/start"), "admin tasdig'i kerak")
        self.assertSent(await self.send(user, text="Ali Valiyev"), "Telefon raqamni yuborish")
        sent = await self.send(user, contact={"phone_number": "998901234567", "first_name": "Ali", "user_id": user})
        self.assertSent(sent, "apply:send")

        sent = await self.press(user, "apply:send")
        card = self.sent_to(sent, OWNER)
        self.assertTrue(card, "the admin gets the application card")
        app_id = (await db.get_pending_applications())[0][0]

        self.assertSent(self.sent_to(await self.press(OWNER, f"review:ok:{app_id}"), user), "tasdiqlandi")

        sent = await self.send(user, text="/start")
        self.assertSent(sent, "Qaysi bo'limda ishlaysiz", f"dept:{await self.department_id('Haydovchi')}")
        sent = await self.press(user, f"dept:{await self.department_id('Haydovchi')}")
        self.assertSent(sent, "Bo'lim saqlandi: Haydovchi", "dumaloq video")  # Zavod chosen automatically

        sent = await self.send(user, **self.VIDEO)
        self.assertEqual(len(self.sent_to(sent, ZAVOD_GROUP, "video")), 1)

    async def test_savdo_employee_chooses_a_shop(self):
        await self.make_employee(2002, "Savdo Xodim", "Savdo")
        sent = await self.send(2002, text="/start")
        for name in SHOP_POINTS:
            self.assertSent(sent, f"loc:{await self.location_id(name)}")
        self.assertNotSent(sent, f"loc:{await self.location_id('Zavod')}")

    async def test_department_is_not_changed_from_an_old_keyboard(self):
        await self.make_employee(2003, "Xodim", "Haydovchi")
        sent = await self.press(2003, f"dept:{await self.department_id('Savdo')}")
        self.assertSent(sent, "allaqachon tanlangan: Haydovchi")

    async def test_buttons_from_the_old_version_and_forged_buttons(self):
        await self.make_employee(2004, "Xodim", "Haydovchi")
        self.assertSent(await self.press(2004, "branch_Eski bozor"), "eskirgan")
        self.assertSent(await self.press(2004, "dept_Savdo"), "eskirgan")
        # a shop of another department cannot be chosen
        self.assertSent(await self.press(2004, f"loc:{await self.location_id('Eski bozor')}"), "mavjud emas")
        self.assertSent(await self.press(2004, "loc:abc"), "mavjud emas")

    async def test_owner_who_is_not_an_employee_gets_the_admin_menu(self):
        self.assertSent(await self.send(OWNER, text="/start"), "Salom, xo'jayin", "🗂 Bo'lim va filiallar")


# =====================================================================
# 3. Attendance
# =====================================================================
class AttendanceTests(FlowTestCase):
    async def _savdo_at(self, user_id: int, shop: str):
        await self.make_employee(user_id, "Savdo Xodim", "Savdo")
        await self.send(user_id, text="/start")
        await self.press(user_id, f"loc:{await self.location_id(shop)}")

    async def test_check_in_and_out_survive_a_restart(self):
        await self._savdo_at(3001, "Eski bozor")
        sent = await self.check_in(3001, SHOP_POINTS["Eski bozor"])
        self.assertSent(sent, "Kelganingiz qayd etildi")
        self.assertSent(self.sent_to(sent, SAVDO_GROUP), "Keldi", "Eski bozor")
        self.assertEqual(await self.query("SELECT branch, location_id FROM attendance"),
                         [("Eski bozor", await self.location_id("Eski bozor"))])

        self.restart()  # FSM memory is lost; the shop comes from the database
        sent = await self.send(3001, **self.VIDEO)
        self.assertSent(sent, "📍 Ketdim")
        await self.send(3001, text="📍 Ketdim")
        sent = await self.send(3001, **self.live(SHOP_POINTS["Eski bozor"]))
        self.assertSent(sent, "Ketganingiz qayd etildi")
        self.assertSent(self.sent_to(sent, SAVDO_GROUP), "Ketdi", "Ishlagan vaqti")

    async def test_location_rules(self):
        await self._savdo_at(3002, "Eski bozor")
        await self.send(3002, **self.VIDEO)
        await self.send(3002, text="📍 Keldim")
        self.assertSent(await self.send(3002, **self.live(FAR_AWAY)), "hududida emassiz")
        static = {"location": {"latitude": SHOP_POINTS["Eski bozor"][0], "longitude": SHOP_POINTS["Eski bozor"][1]}}
        self.assertSent(await self.send(3002, **static), "statik")
        self.assertEqual(await self.query("SELECT COUNT(*) FROM attendance"), [(0,)])

    async def test_second_shift_on_the_same_day_is_refused(self):
        await self._savdo_at(3003, "Koson Lyuba")
        await self.check_in(3003, SHOP_POINTS["Koson Lyuba"])
        await self.check_out(3003, SHOP_POINTS["Koson Lyuba"])
        self.assertSent(await self.send(3003, **self.VIDEO), "allaqachon yakunlagansiz")

    async def test_auto_close_after_checkout_request(self):
        await self.make_employee(3004, "Zavod Xodim", "Haydovchi")
        await self.send(3004, text="/start")
        await self.check_in(3004, ZAVOD_POINT)
        await self.send(3004, **self.VIDEO)  # intention to leave, "Ketdim" never pressed
        past = (datetime.now() - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
        await self.query("UPDATE attendance SET checkout_requested_at = ?", past)

        start = len(self.log)
        await auto_close._check_and_close(self.bot)
        sent = self.log[start:]
        self.assertSent(self.sent_to(sent, ZAVOD_GROUP), "Avtomatik yopildi", "Zavod")
        self.assertSent(self.sent_to(sent, 3004), "avtomatik yakunlandi")
        self.assertEqual(await self.query("SELECT auto_closed, check_out_time FROM attendance"), [(1, past)])

    async def test_auto_close_group_failure_does_not_stop_the_rest(self):
        await self.make_employee(3005, "Zavod Xodim", "Haydovchi")
        await self.send(3005, text="/start")
        await self.check_in(3005, ZAVOD_POINT)
        await self.query("UPDATE attendance SET checkout_requested_at = '2026-01-01 10:00:00'")
        with mock.patch("services.telegram_group.send_auto_close", side_effect=RuntimeError("network")):
            start = len(self.log)
            await auto_close._check_and_close(self.bot)
        self.assertSent(self.sent_to(self.log[start:], 3005), "avtomatik yakunlandi")

    async def test_check_out_after_auto_close_does_not_overwrite_the_record(self):
        await self.make_employee(3006, "Zavod Xodim", "Haydovchi")
        await self.send(3006, text="/start")
        await self.check_in(3006, ZAVOD_POINT)
        await self.send(3006, **self.VIDEO)
        await self.send(3006, text="📍 Ketdim")
        await self.query("UPDATE attendance SET check_out_time = '2026-01-01 10:00:00', auto_closed = 1")
        sent = await self.send(3006, **self.live(ZAVOD_POINT))
        self.assertSent(sent, "allaqachon yopilgan")
        self.assertEqual(await self.query("SELECT check_out_time FROM attendance"), [("2026-01-01 10:00:00",)])


# =====================================================================
# 4. Admin: departments, locations, groups
# =====================================================================
class CatalogTests(FlowTestCase):
    async def test_new_location_from_a_map_link_reaches_employees(self):
        self.assertSent(await self.send(OWNER, text="🗂 Bo'lim va filiallar"), "4 faol / 4")
        await self.press(OWNER, "ct:newloc")
        self.assertSent(await self.send(OWNER, text="eski BOZOR"), "Bu nom band")
        self.assertSent(await self.send(OWNER, text="Qarshi  do'koni"), "joylashuvini yuboring")
        self.assertSent(await self.send(OWNER, text="salom"), "aniqlab bo'lmadi")
        self.assertSent(await self.send(OWNER, text="https://example.com/?q=38.8,65.7"), "Faqat Google yoki Yandex")
        sent = await self.send(OWNER, text=YANDEX_LINK)
        self.assertIn({"type": "pin", "lat": 38.829001, "lon": 65.793878}, sent)

        await self.bot_added_to_group(-100333, "Qarshi guruhi")
        sent = await self.press(OWNER, "ct:ptok")
        self.assertSent(sent, "Savdo guruhi", "Zavod guruhi", "Qarshi guruhi", "Guruhsiz")
        await self.press(OWNER, "ct:g:-100333")
        await self.press(OWNER, f"ct:chk:{await self.department_id('Savdo')}")
        sent = await self.press(OWNER, "ct:chkok")
        self.assertSent(sent, "Filial yaratildi", "Qarshi guruhi", "Bo'limlar: Savdo")
        self.assertSent(await self.press(OWNER, "ct:chkok"), "eskirgan")  # double tap

        shop = await self.location_id("Qarshi do'koni")
        self.assertEqual((await db.get_location(shop))[2:5], (38.829001, 65.793878, -100333))

        await self.make_employee(4001, "Savdo Xodim", "Savdo")
        self.assertSent(await self.send(4001, text="/start"), f"loc:{shop}")
        await self.press(4001, f"loc:{shop}")
        sent = await self.check_in(4001, (38.829001, 65.793878))
        self.assertSent(self.sent_to(sent, -100333), "Keldi", "Qarshi do'koni")

    async def test_rename_and_move_keep_history(self):
        await self.make_employee(4002, "Savdo Xodim", "Savdo")
        shop = await self.location_id("Eski bozor")
        await self.send(4002, text="/start")
        await self.press(4002, f"loc:{shop}")
        await self.check_in(4002, SHOP_POINTS["Eski bozor"])

        await self.press(OWNER, f"ct:lname:{shop}")
        self.assertSent(await self.send(OWNER, text="Eski bozor markaz"), "Eski bozor markaz")
        self.assertEqual(await self.query("SELECT branch FROM attendance"), [("Eski bozor",)])  # history
        self.assertEqual(await self.query("SELECT last_branch FROM employees WHERE user_id = 4002"),
                         [("Eski bozor markaz",)])

        await self.press(OWNER, f"ct:lpoint:{shop}")
        lat, lon = SHOP_POINTS["Eski bozor"]
        sent = await self.send(OWNER, text=f"{lat + 0.001}, {lon}")
        self.assertSent(sent, "Eski joydan 111 metr")
        await self.press(OWNER, "ct:ptok")
        self.assertAlmostEqual((await db.get_location(shop))[2], lat + 0.001, places=6)

        sent = await self.check_out(4002, (lat + 0.001, lon))
        self.assertSent(sent, "Eski bozor markaz hududida tasdiqlandi")

        await self.press(OWNER, f"ct:dname:{await self.department_id('Savdo')}")
        await self.send(OWNER, text="Savdo bo'limi")
        self.assertEqual(await self.query("SELECT department FROM employees WHERE user_id = 4002"),
                         [("Savdo bo'limi",)])

    async def test_changes_that_would_strand_employees_are_refused(self):
        await self.make_employee(4003, "Zavod Xodim", "Haydovchi")
        zavod, haydovchi, savdo = (await self.location_id("Zavod"), await self.department_id("Haydovchi"),
                                   await self.department_id("Savdo"))

        self.assertSent(await self.press(OWNER, f"ct:loff:{zavod}"), "yagona filiali", "Haydovchi")
        self.assertSent(await self.press(OWNER, f"ct:doff:{haydovchi}"), "1 ta faol xodim")

        await self.press(OWNER, f"ct:ldeps:{zavod}")
        await self.press(OWNER, f"ct:chk:{haydovchi}")
        self.assertSent(await self.press(OWNER, "ct:chkok"), "Saqlab bo'lmaydi")
        await self.press(OWNER, "ct:cancel")

        await self.make_employee(4004, "Savdo Xodim", "Savdo")
        await self.press(OWNER, f"ct:dlocs:{savdo}")
        for row in await db.get_department_locations(savdo):
            await self.press(OWNER, f"ct:chk:{row[0]}")
        self.assertSent(await self.press(OWNER, "ct:chkok"), "Kamida bitta faol filial")
        self.assertEqual(len(await db.get_department_locations(savdo)), 3)

    async def test_open_shift_finishes_at_a_deactivated_location(self):
        await self.make_employee(4005, "Savdo Xodim", "Savdo")
        shop = await self.location_id("Lyuba Orzu bozor")
        await self.send(4005, text="/start")
        await self.press(4005, f"loc:{shop}")
        await self.check_in(4005, SHOP_POINTS["Lyuba Orzu bozor"])

        await self.press(OWNER, f"ct:loff:{shop}")
        self.assertSent(await self.press(OWNER, f"ct:loffok:{shop}"), "faol emas")

        self.restart()
        sent = await self.check_out(4005, SHOP_POINTS["Lyuba Orzu bozor"])
        self.assertSent(sent, "Lyuba Orzu bozor hududida tasdiqlandi")
        self.assertSent(self.sent_to(sent, SAVDO_GROUP), "Ketdi")

        sent = await self.send(4005, text="/start")
        self.assertNotSent(sent, f"loc:{shop}")  # no longer offered
        await self.press(OWNER, f"ct:lon:{shop}")
        self.assertSent(await self.send(4005, text="/start"), f"loc:{shop}")

    async def test_delete_only_what_was_never_used(self):
        used = await self.location_id("Eski bozor")
        await self.make_employee(4006, "Savdo Xodim", "Savdo")
        await self.send(4006, text="/start")
        await self.press(4006, f"loc:{used}")
        self.assertNotSent(await self.press(OWNER, f"ct:loc:{used}"), f"ct:ldel:{used}")
        self.assertSent(await self.press(OWNER, f"ct:ldelok:{used}"), "O'chirib bo'lmaydi")
        self.assertSent(await self.press(OWNER, f"ct:ddelok:{await self.department_id('Savdo')}"), "O'chirib bo'lmaydi")

        await self.press(OWNER, "ct:newdep")
        await self.send(OWNER, text="Qorovul")
        await self.press(OWNER, f"ct:chk:{await self.location_id('Zavod')}")
        self.assertSent(await self.press(OWNER, "ct:chkok"), "Bo'lim yaratildi", "avtomatik tanlanadi")
        guard = await self.department_id("Qorovul")
        await self.press(OWNER, f"ct:ddel:{guard}")
        await self.press(OWNER, f"ct:ddelok:{guard}")
        self.assertIsNone(await db.get_department_by_name("Qorovul"))
        self.assertEqual(await self.query("SELECT COUNT(*) FROM department_locations WHERE department_id = ?", guard),
                         [(0,)])

    async def test_groups_are_remembered_automatically(self):
        await self.bot_added_to_group(-100444, "Yangi guruh")
        self.assertEqual(await db.get_group(-100444), (-100444, "Yangi guruh", 1))
        await self.group_event(-100444, new_chat_title="Qayta nomlangan")
        self.assertEqual((await db.get_group(-100444))[1], "Qayta nomlangan")

        shop = await self.location_id("Koson Lyuba")
        await db.update_location_group(shop, -100444)
        await self.group_event(-100444, migrate_to_chat_id=-100555)  # became a supergroup
        self.assertEqual((await db.get_location(shop))[4], -100555)
        self.assertEqual((await db.get_group(-100444))[2], 0)

    async def test_location_without_a_group_still_works(self):
        shop = await self.location_id("Koson Lyuba")
        await self.press(OWNER, f"ct:lgroup:{shop}")
        self.assertSent(await self.press(OWNER, "ct:g:0"), "biriktirilmagan")
        await self.make_employee(4007, "Savdo Xodim", "Savdo")
        await self.send(4007, text="/start")
        await self.press(4007, f"loc:{shop}")
        sent = await self.check_in(4007, SHOP_POINTS["Koson Lyuba"])
        self.assertSent(sent, "Kelganingiz qayd etildi")
        self.assertFalse([e for e in sent if e.get("type") in ("msg", "video") and e.get("chat") != 4007])

    async def test_wizard_buttons_are_stale_after_a_restart(self):
        await self.press(OWNER, f"ct:lpoint:{await self.location_id('Zavod')}")
        self.restart()
        self.assertSent(await self.press(OWNER, "ct:ptok"), "eskirgan")
        # browsing screens need no memory and keep working
        self.assertSent(await self.press(OWNER, "ct:locs"), "Filiallar")

    async def test_employees_cannot_use_the_admin_catalog(self):
        await self.make_employee(4008, "Xodim", "Savdo")
        for data in ("ct:locs", f"ct:ldelok:{await self.location_id('Zavod')}", "ct:newloc"):
            self.assertFalse([e for e in await self.press(4008, data) if e["type"] == "edit"])
        self.assertNotSent(await self.send(4008, text="/filiallar"), "Bo'lim va filiallar")


# =====================================================================
# 5. Monthly tabel
# =====================================================================
class TabelTests(FlowTestCase):
    async def test_tabel_lists_every_shop_and_the_current_department_name(self):
        from openpyxl import load_workbook

        await self.make_employee(5001, "Tabel Xodim", "Savdo")
        today = datetime.now()
        for day, shop in ((1, "Eski bozor"), (2, "Koson Lyuba"), (3, "Eski bozor")):
            work_date = today.replace(day=day).strftime("%Y-%m-%d")
            await self.query(
                """INSERT INTO attendance (user_id, full_name, branch, work_date, check_in_time, check_out_time,
                                           work_hours, location_id) VALUES (5001, 'Tabel Xodim', ?, ?, ?, ?, 8, ?)""",
                shop, work_date, f"{work_date} 09:00:00", f"{work_date} 17:00:00", await self.location_id(shop),
            )
        await db.rename_department(await self.department_id("Savdo"), "Savdo bo'limi")

        with mock.patch.object(excel, "TABLES_DIR", os.path.dirname(db.DB_PATH)):
            path = await excel.generate_monthly_tabel(today.year, today.month)
        sheet = load_workbook(path).active
        row = next(r for r in sheet.iter_rows(values_only=True) if r[0] == "Tabel Xodim")
        self.assertEqual(row[2], "Savdo bo'limi")
        self.assertEqual(row[3], "Eski bozor, Koson Lyuba")
        header = [cell.value for cell in sheet[1]]
        self.assertEqual(row[header.index("Umumiy ish kuni")], 3)
        self.assertEqual(row[header.index("Umumiy soat")], 24)
