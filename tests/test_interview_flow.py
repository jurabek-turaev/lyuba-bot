"""
Hiring flow, phase 4: the admin side (card buttons, interview wizard, rejection
letter, reschedule request, reminder, result question, hiring).

Run:  python -m unittest discover -s tests -t . -v
"""
import unittest
from datetime import date, datetime, timedelta

import database as db
from handlers import interview
from handlers.application import send_application_card
from services import hr_messages, hr_tasks
from tests.flow_harness import OWNER, FlowTestCase

USER = 5001
LETTER = (
    "Hurmatli Valiyev Ali,\n\n"
    "Lyuba kompaniyasidagi Savdo lavozimiga ariza topshirganingiz va jamoamizga qo‘shilishga qiziqish "
    "bildirganingiz uchun tashakkur.\n"
    "Arizangizni diqqat bilan ko‘rib chiqqach, ushbu lavozim bo‘yicha hozirgi talablarimizga tajribasi ko‘proq "
    "mos keladigan boshqa nomzodlar bilan jarayonni davom ettirishga qaror qildik.\n"
    "Ariza topshirishga vaqt ajratganingiz uchun minnatdorchilik bildiramiz hamda ish qidirish jarayonida sizga "
    "omad tilaymiz.\n\n"
    "Hurmat bilan,\n"
    "Lyuba jamoasi"
)


def _minute(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")


class HelperTests(unittest.TestCase):
    def test_rejection_letter_is_word_for_word(self):
        self.assertEqual(hr_messages.rejection("Valiyev Ali", "Savdo"), LETTER)
        self.assertIn("Lyuba kompaniyasiga ariza", hr_messages.rejection("Ali", None))

    def test_working_days_skip_sunday_and_a_late_today(self):
        saturday_morning = datetime(2026, 11, 21, 9, 0)
        days = interview.working_days(saturday_morning)
        self.assertEqual(days[0], date(2026, 11, 21))
        self.assertNotIn(date(2026, 11, 22), days)  # Sunday
        self.assertEqual(len(days), 7)
        self.assertEqual(interview.working_days(datetime(2026, 11, 21, 18, 0))[0], date(2026, 11, 23))

    def test_typed_day(self):
        today = date(2026, 11, 22)
        self.assertEqual(interview.parse_day("27.11", today), date(2026, 11, 27))
        self.assertEqual(interview.parse_day("5.1", today), date(2027, 1, 5))  # next year
        self.assertEqual(interview.parse_day("27/11/2026", today), date(2026, 11, 27))
        for bad in ["21.11.2026", "31.02", "tomorrow", "01.06.2027"]:
            with self.assertRaises(ValueError, msg=bad):
                interview.parse_day(bad, today)


class HiringTestCase(FlowTestCase):
    """One new application (a PDF resume for Savdo) and its card already sent to the admin."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.app_id = await db.submit_application(
            USER, "ali", kind=db.KIND_FORM, full_name="Valiyev Ali", phone="+998901234567",
            department_id=await self.department_id("Savdo"), expected_salary=4_000_000,
            start_availability="Darhol", resume_data={"birth_date": "01.01.2000"}, resume_file_id="pdf",
            resume_file_name="Rezyume.pdf", resume_file_type="document",
        )
        await send_application_card(self.bot, self.app_id)
        self.log.clear()

    async def invite_directly(self, when: datetime, invited_ago=timedelta(days=3)):
        await db.invite_to_interview(self.app_id, _minute(when), "Eski bozor",
                                     await self.location_id("Eski bozor"), "Pasport olib keling")
        await self.query("UPDATE applications SET invited_at = ? WHERE id = ?",
                         (datetime.now() - invited_ago).strftime("%Y-%m-%d %H:%M:%S"), self.app_id)


class InterviewFlowTests(HiringTestCase):
    async def test_invite_wizard(self):
        a = self.app_id
        sent = await self.press(OWNER, f"hr:inv:{a}")
        self.assertSent(sent, "Suhbatga taklif", "Suhbat kunini tanlang", "iv:day:other")
        day = interview.working_days(datetime.now())[1]
        self.assertSent(await self.press(OWNER, f"iv:day:{day:%Y%m%d}"), "Soatni tanlang", "iv:hour:10")
        await self.press(OWNER, "iv:hour:10")
        self.assertSent(await self.press(OWNER, "iv:to:hour"), "Soatni tanlang")  # back
        await self.press(OWNER, "iv:hour:10")
        sent = await self.press(OWNER, "iv:min:30")
        self.assertSent(sent, "qayerda", f"iv:loc:{await self.location_id('Eski bozor')}", "iv:loc:other")
        await self.press(OWNER, f"iv:loc:{await self.location_id('Eski bozor')}")
        sent = await self.press(OWNER, "iv:note:0")
        self.assertSent(sent, "Kadrga quyidagi xabar boradi", "Sizni suhbatga taklif qilamiz", "10:30",
                        "Eski bozor", "yandex.uz/maps", "Pasport olib keling", "iv:send")

        sent = await self.press(OWNER, "iv:send")
        to_candidate = self.sent_to(sent, USER)
        self.assertSent(to_candidate, "Hurmatli Valiyev Ali", "suhbatga taklif qilamiz", f"rq:{a}")
        self.assertNotSent(to_candidate, "Kelaman")
        self.assertTrue([e for e in sent if e["type"] == "pin"], "the place is sent as a map pin")
        self.assertSent(sent, "Taklif yuborildi", "Suhbat:", f"hr:hire:{a}", f"hr:move:{a}", f"hr:noshow:{a}")

        app = await db.get_application_details(a)
        self.assertEqual((app["status"], app["interview_at"], app["interview_note"]),
                         ("invited", f"{day:%Y-%m-%d} 10:30", "Pasport olib keling"))
        self.assertSent(await self.send(USER, text="/start"), "suhbatga taklif qilingansiz", "10:30")

    async def test_typed_date_place_and_note(self):
        await self.press(OWNER, f"hr:inv:{self.app_id}")
        await self.press(OWNER, "iv:day:other")
        self.assertSent(await self.send(OWNER, text="ertaga"), "kun.oy")
        day = date.today() + timedelta(days=10)
        self.assertSent(await self.send(OWNER, text=f"{day:%d.%m.%Y}"), "Soatni tanlang")
        await self.press(OWNER, "iv:hour:15")
        await self.press(OWNER, "iv:min:00")
        await self.press(OWNER, "iv:loc:other")
        await self.send(OWNER, text="Qarshi, Mustaqillik ko'chasi 5")
        await self.press(OWNER, "iv:note:own")
        sent = await self.send(OWNER, text="Ikkinchi qavatga ko'tariling")
        self.assertSent(sent, "Mustaqillik", "Ikkinchi qavat")
        self.assertNotSent(sent, "yandex.uz")  # a typed address has no map
        sent = await self.press(OWNER, "iv:send")
        self.assertFalse([e for e in sent if e["type"] == "pin"])
        self.assertEqual((await db.get_application_details(self.app_id))["interview_at"], f"{day:%Y-%m-%d} 15:00")

    async def test_wizard_after_a_restart_is_stale(self):
        await self.press(OWNER, f"hr:inv:{self.app_id}")
        self.restart()
        self.assertSent(await self.press(OWNER, "iv:hour:10"), "eskirgan")

    async def test_candidate_asks_for_another_time_and_admin_moves_it(self):
        await self.invite_directly(datetime.now() + timedelta(days=2))
        a = self.app_id
        self.assertSent(await self.press(USER, f"rq:{a}"), "Qaysi kun va soat")
        sent = await self.send(USER, text="Payshanba 15:00 dan keyin")
        self.assertSent(self.sent_to(sent, USER), "adminga yuborildi")
        self.assertSent(self.sent_to(sent, OWNER), "o'zgartirishni so'radi", "Payshanba", f"hr:move:{a}")
        self.assertSent(sent, "boshqa vaqt so'radi")  # the card shows the wish

        self.assertSent(await self.press(OWNER, f"hr:move:{a}"), "Suhbatni ko'chirish")
        day = interview.working_days(datetime.now())[3]
        await self.press(OWNER, f"iv:day:{day:%Y%m%d}")
        await self.press(OWNER, "iv:hour:16")
        await self.press(OWNER, "iv:min:00")
        await self.press(OWNER, f"iv:loc:{await self.location_id('Zavod')}")
        await self.press(OWNER, "iv:note:none")
        sent = await self.press(OWNER, "iv:send")
        self.assertSent(self.sent_to(sent, USER), "Suhbat vaqti o'zgartirildi", "16:00", "Zavod")
        app = await db.get_application_details(a)
        self.assertEqual((app["reschedule_request"], app["interview_place"]), (None, "Zavod"))

    async def test_reject_with_a_reason(self):
        a = self.app_id
        self.assertSent(await self.press(OWNER, f"hr:no:{a}"), "Sababini tanlang", f"rj:{a}:x")
        sent = await self.press(OWNER, f"rj:{a}:0")
        self.assertSent(sent, "Tajriba yetarli emas", "Kadrga quyidagi xabar boradi", f"rj:{a}:0:go")
        await self.press(OWNER, f"rj:{a}:r")  # change the reason
        await self.press(OWNER, f"rj:{a}:2")
        sent = await self.press(OWNER, f"rj:{a}:2:go")
        letter = self.sent_to(sent, USER)
        self.assertEqual(letter[0]["text"], LETTER)
        self.assertSent(sent, "Rad etildi", "sabab: Lavozim band")
        app = await db.get_application_details(a)
        self.assertEqual((app["status"], app["reject_reason"]), ("rejected", "Lavozim band"))
        self.assertSent(await self.press(OWNER, f"rj:{a}:2:go"), "allaqachon")
        self.assertSent(await self.send(USER, text="/start"), "7 kun")

    async def test_no_show_only_after_the_interview(self):
        a = self.app_id
        await self.invite_directly(datetime.now() + timedelta(hours=3))
        self.assertSent(await self.press(OWNER, f"hr:noshow:{a}"), "hali kelmagan")
        await self.query("UPDATE applications SET interview_at = ? WHERE id = ?",
                         _minute(datetime.now() - timedelta(hours=2)), a)
        self.assertSent(await self.press(OWNER, f"hr:noshow:{a}"), f"hr:noshow_ok:{a}", f"hr:back:{a}")
        self.assertSent(await self.press(OWNER, f"hr:back:{a}"), f"hr:hire:{a}")
        sent = await self.press(OWNER, f"hr:noshow_ok:{a}")
        self.assertSent(self.sent_to(sent, USER), "suhbatga kelmaganingiz")
        self.assertSent(sent, "Suhbatga kelmadi")
        self.assertEqual((await db.get_application_details(a))["status"], "no_show")

    async def test_block_tells_the_candidate_nothing(self):
        a = self.app_id
        await self.press(OWNER, f"hr:ban:{a}")
        sent = await self.press(OWNER, f"hr:ban_ok:{a}")
        self.assertEqual(self.sent_to(sent, USER), [])
        self.assertSent(sent, "Bloklandi")
        self.assertEqual(await self.send(USER, text="/start"), [])

    async def test_hire_after_the_interview(self):
        a = self.app_id
        await self.invite_directly(datetime.now() - timedelta(hours=2))
        await self.press(OWNER, f"hr:hire:{a}")
        sent = await self.press(OWNER, f"hr:hire_ok:{a}")
        self.assertSent(self.sent_to(sent, USER), "Tabriklaymiz", "Savdo", "/start")
        self.assertSent(self.sent_to(sent, OWNER), "ishga qabul qilindi", "Ish vaqtini belgilash")
        self.assertSent(sent, "Ishga qabul qilindi")  # the card
        rows = await self.query("SELECT department FROM employees WHERE user_id = ?", USER)
        self.assertEqual(rows, [("Savdo",)])
        self.assertSent(await self.press(OWNER, f"hr:hire_ok:{a}"), "allaqachon")

    async def test_reminder_the_day_before(self):
        tomorrow_10 = datetime.combine(date.today() + timedelta(days=1), datetime.min.time()).replace(hour=10)
        await self.invite_directly(tomorrow_10)
        today_17_59 = tomorrow_10.replace(hour=17, minute=59) - timedelta(days=1)
        await hr_tasks.send_reminders(self.bot, today_17_59)
        self.assertEqual(self.sent_to(self.log, USER), [])

        await hr_tasks.send_reminders(self.bot, today_17_59 + timedelta(minutes=1))
        self.assertSent(self.sent_to(self.log, USER), "ertaga suhbatingiz bor", "10:00", "Eski bozor")
        self.assertTrue([e for e in self.log if e["type"] == "pin"])
        self.log.clear()
        await hr_tasks.send_reminders(self.bot, today_17_59 + timedelta(minutes=30))
        self.assertEqual(self.log, [], "only once")

    async def test_no_reminder_after_a_fresh_invitation(self):
        tomorrow_10 = datetime.combine(date.today() + timedelta(days=1), datetime.min.time()).replace(hour=10)
        await self.invite_directly(tomorrow_10, invited_ago=timedelta(0))
        await hr_tasks.send_reminders(self.bot, tomorrow_10 - timedelta(hours=1))
        self.assertEqual(self.sent_to(self.log, USER), [])
        self.assertIsNotNone((await db.get_application_details(self.app_id))["reminder_sent_at"])

    async def test_admin_is_asked_about_the_result_once(self):
        a = self.app_id
        await self.invite_directly(datetime.now() - timedelta(minutes=30))
        await hr_tasks.ask_results(self.bot)
        self.assertEqual(self.log, [], "not before RESULT_DELAY")
        await self.query("UPDATE applications SET interview_at = ? WHERE id = ?",
                         _minute(datetime.now() - timedelta(hours=2)), a)
        await hr_tasks.ask_results(self.bot)
        self.assertSent(self.sent_to(self.log, OWNER), "Suhbat vaqti o'tdi", f"hr:hire:{a}", f"hr:noshow:{a}")
        self.log.clear()
        await hr_tasks.ask_results(self.bot)
        self.assertEqual(self.log, [])

        # moving the interview asks again after the new time
        await self.invite_directly(datetime.now() - timedelta(hours=2))
        await hr_tasks.ask_results(self.bot)
        self.assertSent(self.log, "Suhbat vaqti o'tdi")

    async def test_candidate_withdrawal_updates_the_card(self):
        await self.press(USER, f"rw:{self.app_id}:yes")
        self.assertSent(self.log, "qaytarib oldi", "Kadr arizani qaytarib oldi")


class ApplicationsMenuTests(HiringTestCase):
    async def test_sections(self):
        # one new application (from setUp) and two invited: later today and in 3 days
        second = await db.submit_application(
            USER + 1, None, kind=db.KIND_UPLOAD, full_name="Karimov Bek", phone="+998911112233",
            department_id=await self.department_id("Haydovchi"), expected_salary=6_000_000,
            resume_file_id="scan", resume_file_type="photo")
        third = await db.submit_application(
            USER + 2, None, kind=db.KIND_UPLOAD, full_name="Toshev Sardor", phone="+998931112233",
            department_id=await self.department_id("Savdo"), expected_salary=3_000_000,
            resume_file_id="doc", resume_file_name="cv.pdf", resume_file_type="document")
        later_today = (datetime.now() + timedelta(minutes=5)).replace(second=0, microsecond=0)
        if later_today.date() != date.today():
            later_today = datetime.now().replace(second=0, microsecond=0)
        await db.invite_to_interview(second, _minute(later_today), "Zavod")
        await db.invite_to_interview(third, _minute(datetime.now() + timedelta(days=3)), "Ofis")

        sent = await self.send(OWNER, text="📝 Arizalar")
        self.assertSent(sent, "⏳ Yangi: <b>1</b>", "📅 Suhbat kutilmoqda: <b>2</b>",
                        "📋 Bugungi suhbatlar: <b>1</b>", "Karimov Bek · Haydovchi · Zavod", "al:today")

        new = self.sent_to(await self.press(OWNER, "al:new"), OWNER, "doc")
        self.assertEqual(len(new), 1)
        self.assertSent(new, "Valiyev Ali", f"hr:inv:{self.app_id}")

        invited = await self.press(OWNER, "al:invited")
        cards = [e for e in invited if e["type"] in ("doc", "photo")]
        self.assertEqual([c["caption"].split("\n")[0] for c in cards],
                         [f"📝 <b>Ariza #{second}</b> · Haydovchi", f"📝 <b>Ariza #{third}</b> · Savdo"])
        self.assertSent(cards, f"hr:hire:{second}", "Suhbat:")

        today = await self.press(OWNER, "al:today")
        self.assertEqual(len([e for e in today if e["type"] == "photo"]), 1)

        await db.reject_application(self.app_id)
        self.assertSent(await self.press(OWNER, "al:new"), "ariza yo'q")

    async def test_empty_overview(self):
        await db.reject_application(self.app_id)
        self.assertSent(await self.send(OWNER, text="/arizalar"), "Hozircha javob kutayotgan ariza yo'q")

    async def test_help_mentions_the_hiring_flow(self):
        self.assertSent(await self.send(OWNER, text="/help"), "suhbat kutilmoqda", "18:00")
