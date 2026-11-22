"""
Hiring flow, phase 1: database layer (applications, drafts, interviews, retention).

Run:  python -m unittest discover -s tests -t . -v
"""
import sqlite3
from datetime import datetime, timedelta

import database as db
from services import hr_tasks
from tests.flow_harness import FlowTestCase


def _ago(**delta) -> str:
    return (datetime.now() - timedelta(**delta)).strftime("%Y-%m-%d %H:%M:%S")


class HrMigrationTests(FlowTestCase):
    seed_legacy = False

    async def test_old_applications_survive_and_read_as_legacy(self):
        conn = sqlite3.connect(db.DB_PATH)
        conn.executescript("""
            CREATE TABLE applications (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                full_name TEXT NOT NULL, phone TEXT NOT NULL, username TEXT,
                status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, decided_at TEXT);
            INSERT INTO applications (user_id, full_name, phone, status, created_at)
                VALUES (7, 'Eski Nomzod', '+998901112233', 'pending', '2026-11-01 10:00:00');
        """)
        conn.commit()
        conn.close()

        await db.init_db()
        await db.init_db()  # every restart repeats the migration

        self.assertEqual(await db.get_application(1),
                         (1, 7, "Eski Nomzod", "+998901112233", None, "pending", "2026-11-01 10:00:00"))
        app = await db.get_application_details(1)
        self.assertEqual((app["kind"], app["department_id"], app["resume_data"]), ("legacy", None, None))
        self.assertEqual((await db.get_active_application(7))["id"], 1)
        self.assertEqual(await self.query("SELECT kind FROM applications"), [(None,)])  # stored data untouched

        # an old pending application still blocks a second one
        self.assertIsNone(await db.create_application(7, "Eski Nomzod", "+998901112233", None))


class HrDatabaseTests(FlowTestCase):
    USER = 3001

    async def _submit(self, user=USER, department="Savdo", **extra):
        return await db.submit_application(
            user, "nomzod", kind=extra.pop("kind", db.KIND_FORM), full_name="Ali Valiyev",
            phone="+998901234567", department_id=await self.department_id(department),
            expected_salary=5_000_000, start_availability="Darhol", **extra,
        )

    async def test_draft_is_kept_until_the_application_is_sent(self):
        self.assertIsNone(await db.get_draft(self.USER))
        await db.save_draft(self.USER, db.KIND_FORM, "birth_date", {"full_name": "Gʻayratov Oʻktam"})
        kind, step, data, _ = await db.get_draft(self.USER)
        self.assertEqual((kind, step, data), ("form", "birth_date", {"full_name": "Gʻayratov Oʻktam"}))

        await db.save_draft(self.USER, db.KIND_FORM, "phone", {"full_name": "X", "jobs": [{"company": "A"}]})
        self.assertEqual((await db.get_draft(self.USER))[1:3], ("phone", {"full_name": "X", "jobs": [{"company": "A"}]}))

        app_id = await self._submit(resume_data={"education": [{"place": "TATU"}]}, resume_file_id="FILE1")
        self.assertIsNotNone(app_id)
        self.assertIsNone(await db.get_draft(self.USER), "sending deletes the draft")

        app = await db.get_application_details(app_id)
        self.assertEqual(app["status"], "pending")
        self.assertEqual(app["position"], "Savdo")
        self.assertEqual(app["expected_salary"], 5_000_000)
        self.assertEqual(app["resume_data"], {"education": [{"place": "TATU"}]})

    async def test_second_application_is_refused_while_one_is_active(self):
        await self._submit()
        await db.save_draft(self.USER, db.KIND_UPLOAD, None, {"a": 1})
        self.assertIsNone(await self._submit(kind=db.KIND_UPLOAD))
        self.assertIsNotNone(await db.get_draft(self.USER), "a refused submit keeps the draft")

    async def test_interview_reschedule_and_reminder(self):
        app_id = await self._submit()
        self.assertFalse(await db.request_reschedule(app_id, "Ertaga"), "not invited yet")

        self.assertTrue(await db.invite_to_interview(app_id, "2026-11-25 10:00", "Eski bozor",
                                                     await self.location_id("Eski bozor"), "Pasport olib keling"))
        self.assertTrue(await db.mark_reminder_sent(app_id))
        self.assertFalse(await db.mark_reminder_sent(app_id), "the reminder goes only once")
        self.assertTrue(await db.request_reschedule(app_id, "Payshanba 15:00 dan keyin"))
        self.assertEqual((await db.get_application_details(app_id))["reschedule_request"], "Payshanba 15:00 dan keyin")

        # moving the interview resets the reminder and the request
        self.assertTrue(await db.invite_to_interview(app_id, "2026-11-27 15:30", "Ofis"))
        app = await db.get_application_details(app_id)
        self.assertEqual((app["status"], app["interview_at"], app["interview_place"]), ("invited", "2026-11-27 15:30", "Ofis"))
        self.assertIsNone(app["reminder_sent_at"])
        self.assertIsNone(app["reschedule_request"])
        self.assertIsNone(app["interview_location_id"])

        found = await db.get_interviews_between("2026-11-27 00:00", "2026-11-28 00:00")
        self.assertEqual([a["id"] for a in found], [app_id])
        self.assertEqual(await db.get_interviews_between("2026-11-25 00:00", "2026-11-26 00:00"), [])
        self.assertEqual(await db.count_applications_by_status(), {"invited": 1})

    async def test_final_decisions_apply_only_once(self):
        app_id = await self._submit()
        self.assertFalse(await db.mark_no_show(app_id), "no-show needs an interview")
        await db.invite_to_interview(app_id, "2026-11-25 10:00", "Ofis")
        self.assertTrue(await db.reject_application(app_id, "Tajriba yetarli emas"))
        self.assertFalse(await db.reject_application(app_id))
        self.assertIsNone(await db.approve_application(app_id))
        self.assertFalse(await db.cancel_application(app_id))
        app = await db.get_application_details(app_id)
        self.assertEqual((app["status"], app["reject_reason"]), ("rejected", "Tajriba yetarli emas"))
        self.assertIsNone(await db.get_active_application(self.USER))

        second = await self._submit()
        self.assertTrue(await db.cancel_application(second))
        third = await self._submit()
        await db.invite_to_interview(third, "2026-11-25 10:00", "Ofis")
        self.assertTrue(await db.mark_no_show(third))
        self.assertEqual([a["id"] for a in await db.list_applications(["rejected", "no_show"])], [app_id, third])

    async def test_hiring_sets_the_department_from_the_position(self):
        app_id = await self._submit(department="Haydovchi")
        await db.invite_to_interview(app_id, "2026-11-25 10:00", "Zavod")
        self.assertEqual(await db.approve_application(app_id), (self.USER, "Ali Valiyev"))
        rows = await self.query("SELECT full_name, department, department_id, is_active FROM employees WHERE user_id = ?", self.USER)
        self.assertEqual(rows, [("Ali Valiyev", "Haydovchi", await self.department_id("Haydovchi"), 1)])

        sent = await self.send(self.USER, text="/start")
        self.assertNotSent(sent, "Qaysi bo'limda ishlaysiz")  # no department question after hiring

    async def test_returning_employee_moves_to_the_new_department(self):
        await self.make_employee(self.USER, "Ali Valiyev", "Savdo")
        await db.set_employee_location(self.USER, await self.location_id("Eski bozor"))
        await db.deactivate_employee(self.USER)

        app_id = await self._submit(department="Haydovchi")
        await db.approve_application(app_id)
        rows = await self.query("SELECT department, location_id, is_active FROM employees WHERE user_id = ?", self.USER)
        self.assertEqual(rows, [("Haydovchi", None, 1)], "the old shop is not kept for another department")

    async def test_retention_deletes_only_old_closed_applications(self):
        old_rejected = await self._submit(user=1)
        await db.reject_application(old_rejected)
        recent_rejected = await self._submit(user=2)
        await db.reject_application(recent_rejected)
        old_blocked = await self._submit(user=3)
        await db.decide_application(old_blocked, "blocked")
        old_hired = await self._submit(user=4)
        await db.approve_application(old_hired)
        old_pending = await self._submit(user=5)
        await self.query("UPDATE applications SET decided_at = ? WHERE id IN (?, ?, ?)",
                         _ago(days=400), old_rejected, old_blocked, old_hired)
        await self.query("UPDATE applications SET created_at = ? WHERE id = ?", _ago(days=400), old_pending)

        await db.save_draft(10, db.KIND_FORM, "x", {})
        await db.save_draft(11, db.KIND_FORM, "x", {})
        await self.query("UPDATE application_drafts SET updated_at = ? WHERE user_id = 10", _ago(days=31))

        await hr_tasks.purge_expired_data()

        left = {row[0] for row in await self.query("SELECT id FROM applications")}
        self.assertEqual(left, {recent_rejected, old_blocked, old_hired, old_pending})
        self.assertIsNone(await db.get_draft(10))
        self.assertIsNotNone(await db.get_draft(11))


class ReapplyCooldownTests(FlowTestCase):
    USER = 3100

    async def _rejected(self, status: str, days_ago: float):
        app_id = await db.create_application(self.USER, "Ali Valiyev", "+998901234567", None)
        await self.query("UPDATE applications SET status = ?, decided_at = ? WHERE id = ?",
                         status, _ago(days=days_ago), app_id)

    async def test_rejected_candidate_waits_a_week(self):
        await self._rejected("rejected", 3)
        self.assertSent(await self.send(self.USER, text="/start"), "rad etilgan", "4 kun kuting")

    async def test_no_show_candidate_waits_a_week_too(self):
        await self._rejected("no_show", 6.5)
        self.assertSent(await self.send(self.USER, text="/start"), "kuting", "12 soat")

    async def test_after_a_week_the_candidate_may_apply_again(self):
        await self._rejected("rejected", 7.1)
        self.assertSent(await self.send(self.USER, text="/start"), "Ism va Familiyangizni")

    async def test_unblocked_user_may_apply_at_once(self):
        app_id = await db.create_application(self.USER, "Ali Valiyev", "+998901234567", None)
        await db.decide_application(app_id, "blocked")
        self.assertEqual(await self.send(self.USER, text="/start"), [])
        self.assertTrue(await db.unblock_user(self.USER))
        self.assertSent(await self.send(self.USER, text="/start"), "Ism va Familiyangizni")

    async def test_invited_candidate_sees_the_pending_message(self):
        app_id = await db.create_application(self.USER, "Ali Valiyev", "+998901234567", None)
        await db.invite_to_interview(app_id, "2026-11-25 10:00", "Ofis")
        self.assertSent(await self.send(self.USER, text="/start"), "ko'rib chiqilmoqda")
