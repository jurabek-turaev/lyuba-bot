"""
Hiring flow, phase 3: the candidate side (handlers/resume.py).

The PDF renderer is replaced by a fake here (services/resume_pdf has its own tests).

Run:  python -m unittest discover -s tests -t . -v
"""
import unittest
from datetime import date
from unittest import mock

import database as db
from handlers import resume
from services.resume_pdf import ResumeRenderError
from tests.flow_harness import OWNER, FlowTestCase

USER = 4001


class ParserTests(unittest.TestCase):
    def test_salary(self):
        for text, amount in [("5000000", 5_000_000), ("5 000 000", 5_000_000), ("5 000 000 so'm", 5_000_000),
                             ("5.000.000", 5_000_000), ("5,000,000 сум", 5_000_000), ("5 mln", 5_000_000),
                             ("4.5 mln", 4_500_000), ("4,5mln so‘m", 4_500_000)]:
            self.assertEqual(resume.parse_salary(text, {}), amount, text)
        for text in ["besh million", "500", "5 000 000 000 000", ""]:
            with self.assertRaises(resume.InputError, msg=text):
                resume.parse_salary(text, {})

    def test_uzbek_apostrophes(self):
        self.assertEqual(resume.clean_text("  G'ayratov   O`ktam  Ma'ruf "), "Gʻayratov Oʻktam Maʼruf")

    def test_names_dates_years(self):
        self.assertEqual(resume.parse_full_name("valiyev ali", {}), "valiyev ali")
        for bad in ["Ali", "Ali 2", "/start me", "Ali_Vali Karim"]:
            with self.assertRaises(resume.InputError, msg=bad):
                resume.parse_full_name(bad, {})
        self.assertEqual(resume.parse_birth_date("5/3/1998", {}, date(2026, 11, 22)), "05.03.1998")
        for bad in ["1998.03.05", "31.02.1998", "01.01.2015", "01.01.1950"]:
            with self.assertRaises(resume.InputError, msg=bad):
                resume.parse_birth_date(bad, {}, date(2026, 11, 22))
        self.assertEqual(resume.parse_month_year("3.2021", {}), "2021-03")
        with self.assertRaises(resume.InputError):
            resume.parse_job_end("02.2021", {"_item": {"start": "2021-03"}})


class ResumeFlowTests(FlowTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.rendered = []

        async def fake_render(form, photo=None):
            self.rendered.append(form)
            return b"%PDF-fake"

        patcher = mock.patch.object(resume, "render_resume_pdf", side_effect=fake_render)
        self.render = patcher.start()
        self.addCleanup(patcher.stop)

    async def begin(self, kind="form"):
        await self.send(USER, text="/start")
        await self.press(USER, "rf:consent:y")
        return await self.press(USER, f"rf:choose:{kind}")

    async def step(self) -> str:
        return (await db.get_draft(USER))[1]

    async def fill_form(self):
        """Every question answered, ending at the preview. Returns what the preview sent."""
        await self.begin()
        await self.send(USER, text="G'ayratov O'ktam Sho'xrat o'g'li")
        await self.send(USER, text="15.03.1998")
        await self.press(USER, "rf:gender:0")
        await self.send(USER, contact={"phone_number": "998901234567", "first_name": "O", "user_id": USER})
        await self.send(USER, text="Qarshi shahri")
        await self.press(USER, "rf:marital_status:0")
        await self.send(USER, photo=[{"file_id": "face", "file_unique_id": "f", "width": 300, "height": 400}])
        await self.press(USER, f"rf:position:{await self.department_id('Savdo')}")
        await self.send(USER, text="5 000 000")
        await self.press(USER, "rf:start_availability:0")
        await self.press(USER, "rf:education_level:0")
        await self.send(USER, text="Qarshi davlat universiteti")
        await self.send(USER, text="Iqtisodiyot")
        await self.send(USER, text="2019")
        await self.press(USER, "rf:edu_more:n")
        await self.press(USER, "rf:has_experience:y")
        await self.send(USER, text="Makro")
        await self.send(USER, text="Kassir")
        await self.send(USER, text="03.2021")
        await self.press(USER, "rf:job_end:present")
        await self.press(USER, "rf:job_more:y")
        await self.send(USER, text="Korzinka")
        await self.send(USER, text="Sotuvchi")
        await self.send(USER, text="01.2019")
        await self.send(USER, text="02.2021")
        await self.press(USER, "rf:job_more:n")
        await self.press(USER, "rf:lang_name:1")
        await self.press(USER, "rf:lang_level:2")
        await self.press(USER, "rf:lang_more:n")
        await self.press(USER, "rf:computer_skills:>")
        await self.press(USER, "rf:driver_license:1")
        return await self.send(USER, text="Mas'uliyatli va xushmuomala.")

    async def test_full_form_reaches_the_admin_as_a_pdf_card(self):
        sent = await self.fill_form()
        preview = self.sent_to(sent, USER, "doc")
        self.assertEqual(len(preview), 1)
        self.assertTrue(preview[0]["uploaded"])
        self.assertSent(preview, "Rezyumengiz tayyor", "5 000 000 so'm", "Savdo", "rf:preview:send")

        form = self.rendered[0]
        self.assertEqual(form["full_name"], "Gʻayratov Oʻktam Shoʻxrat oʻgʻli")
        self.assertEqual(form["education"], [{"institution": "Qarshi davlat universiteti",
                                              "specialty": "Iqtisodiyot", "year": 2019}])
        self.assertEqual(form["jobs"], [
            {"company": "Makro", "position": "Kassir", "start": "2021-03", "end": "present"},
            {"company": "Korzinka", "position": "Sotuvchi", "start": "2019-01", "end": "2021-02"},
        ])
        self.assertEqual(form["languages"], [{"name": "Rus", "level": "Oʻrta"}])
        self.assertEqual((form["computer_skills"], form["driver_license"], form["photo_file_id"]),
                         (None, "B", "face"))

        sent = await self.press(USER, "rf:preview:send")
        self.assertSent(self.sent_to(sent, USER), "Arizangiz qabul qilindi")
        card = self.sent_to(sent, OWNER, "doc")
        self.assertEqual(len(card), 1)
        self.assertEqual(card[0]["file"], preview[0]["file"])
        self.assertSent(card, "Ariza #1", "Savdo", "28 yosh", "Qarshi shahri", "+998901234567",
                        "5 000 000 so'm", "Darhol", "Oliy", "2 ish joyi", "hr:inv:1", "hr:no:1")

        app = await db.get_application_details(1)
        self.assertEqual((app["kind"], app["status"], app["department_id"]),
                         ("form", "pending", await self.department_id("Savdo")))
        self.assertIsNone(await db.get_draft(USER), "the draft is gone after sending")
        self.assertSent(await self.send(USER, text="/start"), "ko'rib chiqilmoqda", "rw:1")

    async def test_form_survives_a_restart(self):
        await self.begin()
        await self.send(USER, text="Valiyev Ali")
        self.restart()  # FSM memory is lost, the draft is not
        sent = await self.send(USER, text="/start")
        self.assertSent(sent, "tugallanmagan anketa", "rs:continue")
        self.assertSent(await self.press(USER, "rs:continue"), "Tug'ilgan sanangizni", "2/17")
        self.assertSent(await self.send(USER, text="01.01.2000"), "Jinsingiz")

        await self.send(USER, text="/start")
        self.assertSent(await self.press(USER, "rs:restart"), "Shunga rozimisiz")
        self.assertEqual(await self.step(), "consent")

    async def test_wrong_answers_are_explained_and_asked_again(self):
        await self.begin()
        self.assertSent(await self.send(USER, text="Ali"), "to'liq yozing")
        await self.send(USER, text="Valiyev Ali")
        self.assertSent(await self.send(USER, text="2010-01-01"), "kun.oy.yil")
        self.assertSent(await self.send(USER, text="01.01.2015"), "16 dan 70 gacha")
        await self.send(USER, text="01.01.2000")
        self.assertSent(await self.send(USER, text="Erkak"), "tugmalardan birini")
        await self.press(USER, "rf:gender:0")
        stranger = {"phone_number": "998900000000", "first_name": "X", "user_id": 999}
        self.assertSent(await self.send(USER, contact=stranger), "o'zingizning raqamingiz")
        self.assertEqual(await self.step(), "phone")

    async def test_back_button_and_loops(self):
        await self.begin()
        await self.send(USER, text="Valiyev Ali")
        self.assertSent(await self.press(USER, "rf:birth_date:<"), "Familiya, ism")
        await self.send(USER, text="Valiyev Ali")
        self.assertSent(await self.press(USER, "rf:birth_date:<"), "Familiya")  # back works again
        self.assertEqual(await self.step(), "full_name")
        await self.send(USER, text="Valiyev Ali")

        # the "back" text button of the contact question
        await self.send(USER, text="01.01.2000")
        await self.press(USER, "rf:gender:0")
        self.assertSent(await self.send(USER, text=resume.BACK_TEXT), "Jinsingiz")
        await self.press(USER, "rf:gender:1")
        await self.send(USER, contact={"phone_number": "998901234567", "first_name": "A", "user_id": USER})

        # the loop: back from "add another?" keeps the item, back from the next item restores it
        draft = await db.get_draft(USER)
        await db.save_draft(USER, "form", "edu_institution", {**draft[2], "_history": ["education_level"]})
        await self.send(USER, text="TATU")
        await self.press(USER, "rf:edu_specialty:>")
        await self.send(USER, text="2020")
        await self.press(USER, "rf:edu_more:y")
        self.assertEqual((await db.get_draft(USER))[2]["education"], [{"institution": "TATU", "specialty": None,
                                                                       "year": 2020}])
        self.assertSent(await self.press(USER, "rf:edu_institution:<"), "Yana o'quv yurti")
        data = (await db.get_draft(USER))[2]
        self.assertEqual((data["education"], data["_item"]["institution"]), ([], "TATU"))
        await self.press(USER, "rf:edu_more:n")
        self.assertEqual(await self.step(), "has_experience")
        self.assertEqual(len((await db.get_draft(USER))[2]["education"]), 1)

    async def test_stale_buttons(self):
        await self.begin()
        sent = await self.press(USER, "rf:gender:0")
        self.assertSent(sent, "eskirgan")
        sent = await self.press(USER, "rf:choose:form")
        self.assertSent(sent, "eskirgan")

    async def test_edit_a_section_from_the_preview(self):
        await self.fill_form()
        sent = await self.press(USER, "rf:preview:edit")
        self.assertSent(sent, "rf:preview:e_education", "rf:preview:e_skills")
        self.assertSent(await self.press(USER, "rf:preview:e_education"), "Ma'lumotingiz")
        await self.press(USER, "rf:education_level:2")
        sent = await self.press(USER, "rf:edu_institution:>")  # no school this time
        self.assertSent(sent, "Rezyumengiz tayyor")
        self.assertEqual(len(self.rendered), 2, "changed answers make a new PDF")
        self.assertEqual((self.rendered[1]["education_level"], self.rendered[1]["education"]), ("Oʻrta maxsus", []))
        self.assertEqual(len(self.rendered[1]["jobs"]), 2, "other sections are untouched")

        # unchanged answers: the same PDF is shown again without rendering
        await self.press(USER, "rf:preview:edit")
        await self.press(USER, "rf:preview:e_position")
        await self.press(USER, "rf:position:<")  # back from the first question of the section
        self.assertEqual(len(self.rendered), 2)

    async def test_renderer_failure_still_lets_the_candidate_apply(self):
        self.render.side_effect = ResumeRenderError("boom")
        sent = await self.fill_form()
        self.assertSent(sent, "PDF tayyorlashda xatolik", "rf:preview:send")
        sent = await self.press(USER, "rf:preview:send")
        self.assertSent(self.sent_to(sent, OWNER), "Ariza #1", "hr:inv:1")
        self.assertEqual((await db.get_application_details(1))["resume_file_id"], None)

    async def test_upload_own_resume(self):
        await self.begin("upload")
        self.assertSent(await self.send(USER, document={"file_id": "x", "file_unique_id": "x", "file_name": "cv.exe"}),
                        "Faqat PDF")
        big = {"file_id": "x", "file_unique_id": "x", "file_name": "cv.pdf", "file_size": 30 * 1024 * 1024}
        self.assertSent(await self.send(USER, document=big), "juda katta")
        await self.send(USER, photo=[{"file_id": "scan", "file_unique_id": "s", "width": 1, "height": 1}])
        await self.send(USER, text="Valiyev Ali")
        await self.send(USER, contact={"phone_number": "998901234567", "first_name": "A", "user_id": USER})
        await self.press(USER, f"rf:position:{await self.department_id('Savdo')}")
        await self.send(USER, text="3 mln")
        sent = await self.press(USER, "rf:start_availability:3")
        self.assertSent(sent, "Arizangizni tekshiring", "3 000 000 so'm", "1 oydan keyin")
        self.assertEqual(self.rendered, [], "no PDF is made for an uploaded resume")

        card = self.sent_to(await self.press(USER, "rf:preview:send"), OWNER, "photo")
        self.assertEqual(card[0]["file"], "scan")
        self.assertSent(card, "Tayyor rezyume", "3 000 000 so'm")

    async def test_consent_refused_and_draft_cancelled(self):
        await self.send(USER, text="/start")
        self.assertSent(await self.press(USER, "rf:consent:n"), "Fikringiz o'zgarsa")
        self.assertIsNone(await db.get_draft(USER))

        await self.fill_form()
        await self.press(USER, "rf:preview:cancel")
        self.assertSent(await self.press(USER, "rf:preview:cy"), "Anketa o'chirildi")
        self.assertIsNone(await db.get_draft(USER))
        self.assertEqual(await db.count_applications_by_status(), {})

    async def test_candidate_withdraws_the_application(self):
        await self.fill_form()
        await self.press(USER, "rf:preview:send")
        await self.press(USER, "rw:1")
        sent = await self.press(USER, "rw:1:yes")
        self.assertSent(self.sent_to(sent, OWNER), "qaytarib oldi")
        self.assertEqual((await db.get_application_details(1))["status"], "cancelled")
        self.assertSent(await self.send(USER, text="/start"), "Shunga rozimisiz")
        self.assertSent(await self.press(USER, "rw:1:yes"), "allaqachon")

    async def test_stranger_without_a_form_is_pointed_to_start(self):
        self.assertSent(await self.send(USER, text="salom"), "/start")
