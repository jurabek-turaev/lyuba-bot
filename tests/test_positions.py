"""
Positions (job titles inside a department): the admin catalog, the employee's
/start, the candidate's application, the admin's transfer tool and the
"I.T.R xodimlari" -> "Rahbariyat" rename.

Run:  python -m unittest discover -s tests -t . -v
"""
import os
from datetime import datetime
from unittest import mock

import database as db
from handlers import resume
from services import auto_close, excel
from tests.flow_harness import OWNER, SHOP_POINTS, ZAVOD_GROUP, ZAVOD_POINT, FlowTestCase

CANDIDATE = 6001


class PositionTestCase(FlowTestCase):
    async def add_positions(self, department: str, *names) -> list:
        return await db.create_positions(await self.department_id(department), list(names))

    async def position_id(self, department: str, name: str) -> int:
        for position_id, position_name, _ in await db.list_positions(await self.department_id(department),
                                                                      active_only=False):
            if position_name == name:
                return position_id
        raise AssertionError(f"no position {name!r} in {department!r}")


# =====================================================================
# Rename and migration
# =====================================================================
class RenameTests(PositionTestCase):
    async def test_itr_is_rahbariyat_and_old_rows_still_map_to_zavod(self):
        from openpyxl import load_workbook

        names = [row[1] for row in await db.list_departments()]
        self.assertIn("Rahbariyat", names)
        self.assertNotIn("I.T.R xodimlari", names)

        # the database of the previous version: the department still has its old name,
        # an employee in it, an old attendance row with the department name as the branch
        itr = await self.department_id("Rahbariyat")
        await self.query("UPDATE departments SET name = 'I.T.R xodimlari' WHERE id = ?", itr)
        await self.make_employee(7001, "Rahbar Xodim", "I.T.R xodimlari")
        work_date = datetime.now().replace(day=1).strftime("%Y-%m-%d")
        await self.query(
            """INSERT INTO attendance (user_id, full_name, branch, work_date, check_in_time, check_out_time,
                                       work_hours, location_id)
               VALUES (7001, 'Rahbar Xodim', 'I.T.R xodimlari', ?, ?, ?, 8, ?)""",
            work_date, f"{work_date} 09:00:00", f"{work_date} 17:00:00", await self.location_id("Zavod"),
        )
        await db.init_db()  # the next start renames it
        await db.init_db()  # repeating the migration changes nothing
        self.assertEqual(await self.query("SELECT department FROM employees WHERE user_id = 7001"),
                         [("Rahbariyat",)])
        self.assertEqual(len(await db.list_departments()), 4)

        today = datetime.now()
        with mock.patch.object(excel, "TABLES_DIR", os.path.dirname(db.DB_PATH)):
            path = await excel.generate_monthly_tabel(today.year, today.month)
        row = next(r for r in load_workbook(path).active.iter_rows(values_only=True) if r[0] == "Rahbar Xodim")
        self.assertEqual(row[2:5], ("Rahbariyat", "—", "Zavod"))

        # with a position: Bo'lim | Lavozim | Filial
        await self.add_positions("Rahbariyat", "Menejer")
        await db.set_employee_job(7001, itr, await self.position_id("Rahbariyat", "Menejer"))
        with mock.patch.object(excel, "TABLES_DIR", os.path.dirname(db.DB_PATH)):
            path = await excel.generate_monthly_tabel(today.year, today.month)
        sheet = load_workbook(path).active
        self.assertEqual([c.value for c in sheet[1]][2:5], ["Bo'lim", "Lavozim", "Filial"])
        row = next(r for r in sheet.iter_rows(values_only=True) if r[0] == "Rahbar Xodim")
        self.assertEqual(row[2:5], ("Rahbariyat", "Menejer", "Zavod"))

    async def test_an_existing_rahbariyat_is_not_overwritten(self):
        await db.rename_department(await self.department_id("Rahbariyat"), "I.T.R xodimlari")
        await db.create_department("Rahbariyat", [])
        await db.init_db()
        names = [row[1] for row in await db.list_departments()]
        self.assertIn("I.T.R xodimlari", names)
        self.assertEqual(names.count("Rahbariyat"), 1)


# =====================================================================
# Admin catalog
# =====================================================================
class CatalogPositionTests(PositionTestCase):
    async def test_admin_adds_renames_deactivates_and_deletes_positions(self):
        savdo = await self.department_id("Savdo")
        self.assertSent(await self.send(OWNER, text="/filiallar"), "ct:poss", "Lavozimlar: 0 faol")
        self.assertSent(await self.press(OWNER, "ct:poss"), f"ct:dpos:{savdo}", "Savdo (0)")
        self.assertSent(await self.press(OWNER, f"ct:dpos:{savdo}"), "lavozim so'ralmaydi", f"ct:newpos:{savdo}")

        self.assertSent(await self.press(OWNER, f"ct:newpos:{savdo}"), "yangi qatordan")
        self.assertSent(await self.send(OWNER, text="Sotuvchi\nsotuvchi"), "ikki marta")
        sent = await self.send(OWNER, text="Sotuvchi\n\n  Ofitsiant  \nFarrosh")
        self.assertSent(sent, "Sotuvchi", "Ofitsiant", "Farrosh")
        self.assertEqual([p[1] for p in await db.list_positions(savdo)], ["Sotuvchi", "Ofitsiant", "Farrosh"])

        # the same name in the same department is refused (any case), another department may have it
        await self.press(OWNER, f"ct:newpos:{savdo}")
        self.assertSent(await self.send(OWNER, text="SOTUVCHI"), "shunday lavozim bor")
        await self.press(OWNER, "ct:cancel")
        await self.add_positions("Ishlab chiqarish", "Sotuvchi")

        sotuvchi = await self.position_id("Savdo", "Sotuvchi")
        self.assertSent(await self.press(OWNER, f"ct:pos:{sotuvchi}"), "Bo'lim: Savdo", f"ct:pdel:{sotuvchi}")
        await self.press(OWNER, f"ct:pname:{sotuvchi}")
        self.assertSent(await self.send(OWNER, text="Kassir"), "Kassir")
        self.assertEqual((await db.get_position(sotuvchi))[2], "Kassir")

        self.assertSent(await self.press(OWNER, f"ct:poff:{sotuvchi}"), "faol emas", f"ct:pon:{sotuvchi}")
        self.assertEqual([p[1] for p in await db.list_positions(savdo)], ["Ofitsiant", "Farrosh"])
        await self.press(OWNER, f"ct:pon:{sotuvchi}")

        # the department card lists its positions; they are managed only under "💼 Lavozimlar"
        self.assertSent(await self.press(OWNER, f"ct:dep:{savdo}"), "Kassir, Ofitsiant, Farrosh")

        # every page has one back button, to the page it was opened from
        self.assertSent(await self.press(OWNER, f"ct:pos:{sotuvchi}"), '["◀️ Orqaga", "ct:dpos:')
        sent = await self.press(OWNER, f"ct:dpos:{savdo}")
        self.assertSent(sent, '["◀️ Orqaga", "ct:poss"]')
        self.assertNotSent(sent, f"ct:dep:{savdo}")

        # a used position cannot be deleted, only deactivated
        await self.make_employee(7002, "Savdo Xodim", "Savdo")
        await db.set_employee_job(7002, savdo, sotuvchi)
        self.assertNotSent(await self.press(OWNER, f"ct:pos:{sotuvchi}"), f"ct:pdel:{sotuvchi}")
        self.assertSent(await self.press(OWNER, f"ct:pdelok:{sotuvchi}"), "O'chirib bo'lmaydi")
        farrosh = await self.position_id("Savdo", "Farrosh")
        await self.press(OWNER, f"ct:pdel:{farrosh}")
        self.assertSent(await self.press(OWNER, f"ct:pdelok:{farrosh}"), "O'chirildi")
        self.assertIsNone(await db.get_position(farrosh))

    async def test_employees_cannot_manage_positions(self):
        await self.make_employee(7003, "Xodim", "Savdo")
        savdo = await self.department_id("Savdo")
        await self.press(7003, f"ct:newpos:{savdo}")
        await self.send(7003, text="Boshliq")
        self.assertEqual(await db.list_positions(savdo), [])


# =====================================================================
# Employee /start
# =====================================================================
class EmployeeStartTests(PositionTestCase):
    async def test_department_then_position_then_shop(self):
        await self.add_positions("Savdo", "Sotuvchi", "Ofitsiant")
        user = 7101
        await self.make_employee(user, "Yangi Xodim", None)

        sent = await self.send(user, text="/start")
        self.assertSent(sent, "Qaysi bo'limda ishlaysiz")
        sent = await self.press(user, f"dept:{await self.department_id('Savdo')}")
        sotuvchi = await self.position_id("Savdo", "Sotuvchi")
        self.assertSent(sent, "Lavozimingizni tanlang", f"pos:{sotuvchi}", "deptlist")
        self.assertEqual(await db.get_employee_placement(user), (None, None), "nothing is saved before the position")

        # back to the departments and forward again
        self.assertSent(await self.press(user, "deptlist"), "Qaysi bo'limda ishlaysiz")
        await self.press(user, f"dept:{await self.department_id('Savdo')}")
        # a position of another department / a deactivated one cannot be chosen
        await self.add_positions("Ishlab chiqarish", "Pishiriqchi")
        await db.set_position_active(await self.position_id("Savdo", "Ofitsiant"), False)
        self.assertSent(await self.press(user, f"pos:{await self.position_id('Savdo', 'Ofitsiant')}"), "mavjud emas")

        sent = await self.press(user, f"pos:{sotuvchi}")
        self.assertSent(sent, "Saqlandi: Savdo · Sotuvchi", "filialingizni tanlang")
        for name in SHOP_POINTS:
            self.assertSent(sent, f"loc:{await self.location_id(name)}")
        job = await db.get_employee_job(user)
        self.assertEqual(job[2:5], ("Savdo", sotuvchi, "Sotuvchi"))

        # an old keyboard cannot change it
        self.assertSent(await self.press(user, f"pos:{sotuvchi}"), "allaqachon tanlangan")
        self.assertSent(await self.press(user, "deptlist"), "allaqachon tanlangan")

    async def test_factory_position_gets_zavod_automatically(self):
        await self.add_positions("Ishlab chiqarish", "Stanok ustasi", "Pishiriqchi")
        user = 7102
        await self.make_employee(user, "Zavod Xodim", None)
        await self.send(user, text="/start")
        await self.press(user, f"dept:{await self.department_id('Ishlab chiqarish')}")
        sent = await self.press(user, f"pos:{await self.position_id('Ishlab chiqarish', 'Pishiriqchi')}")
        self.assertSent(sent, "Pishiriqchi", "dumaloq video")
        sent = await self.send(user, **self.VIDEO)
        self.assertEqual(len(self.sent_to(sent, ZAVOD_GROUP, "video")), 1)

    async def test_employee_without_a_position_works_as_before(self):
        await self.make_employee(7103, "Eski Xodim", "Ishlab chiqarish")
        await self.add_positions("Ishlab chiqarish", "Pishiriqchi")
        sent = await self.send(7103, text="/start")
        self.assertNotSent(sent, "Lavozim", "Qaysi bo'limda")
        self.assertSent(sent, "dumaloq video")


# =====================================================================
# Candidate's application
# =====================================================================
class ApplicationPositionTests(PositionTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        patcher = mock.patch.object(resume, "render_resume_pdf", return_value=b"%PDF-fake")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def upload_until_department(self):
        await self.send(CANDIDATE, text="/start")
        await self.press(CANDIDATE, "rf:consent:y")
        await self.press(CANDIDATE, "rf:choose:upload")
        await self.send(CANDIDATE, document={"file_id": "cv", "file_unique_id": "cv", "file_name": "cv.pdf"})
        await self.send(CANDIDATE, text="Valiyev Ali")
        return await self.send(CANDIDATE, contact={"phone_number": "998901234567", "first_name": "A",
                                                   "user_id": CANDIDATE})

    async def test_candidate_chooses_department_then_position_and_is_hired_with_it(self):
        await self.add_positions("Savdo", "Sotuvchi", "Ofitsiant")
        savdo = await self.department_id("Savdo")
        sent = await self.upload_until_department()
        self.assertSent(sent, "Qaysi bo'limga ariza", f"rf:department:{savdo}")

        sent = await self.press(CANDIDATE, f"rf:department:{savdo}")
        ofitsiant = await self.position_id("Savdo", "Ofitsiant")
        self.assertSent(sent, "Qaysi lavozimga ariza", f"rf:position:{ofitsiant}", "rf:position:<")
        # same question number for the department and the position
        self.assertSent(sent, "4/6")
        await self.press(CANDIDATE, f"rf:position:{ofitsiant}")
        await self.send(CANDIDATE, text="4 000 000")
        sent = await self.press(CANDIDATE, "rf:start_availability:0")
        self.assertSent(sent, "Bo'lim: <b>Savdo</b>", "Lavozim: <b>Ofitsiant</b>")

        card = self.sent_to(await self.press(CANDIDATE, "rf:preview:send"), OWNER, "doc")
        self.assertSent(card, "Ariza #1</b> · Ofitsiant (Savdo)")
        app = await db.get_application_details(1)
        self.assertEqual((app["department_id"], app["position_id"], app["position"]), (savdo, ofitsiant, "Ofitsiant"))

        # renaming the position later does not change the application's history
        await db.rename_position(ofitsiant, "Afitsiant")
        self.assertEqual((await db.get_application_details(1))["position"], "Ofitsiant")

        await self.press(OWNER, "hr:hire:1")
        self.assertSent(self.sent_to(await self.press(OWNER, "hr:hire_ok:1"), CANDIDATE), "Ofitsiant")
        job = await db.get_employee_job(CANDIDATE)
        self.assertEqual(job[1:5], (savdo, "Savdo", ofitsiant, "Afitsiant"))
        # hired with a position: /start goes straight to the shops
        sent = await self.send(CANDIDATE, text="/start")
        self.assertNotSent(sent, "Qaysi bo'limda", "Lavozimingizni")
        self.assertSent(sent, "filialingizni tanlang")

    async def test_department_without_positions_skips_the_question(self):
        await self.upload_until_department()
        sent = await self.press(CANDIDATE, f"rf:department:{await self.department_id('Rahbariyat')}")
        self.assertSent(sent, "Qancha oylik maosh")
        self.assertNotSent(sent, "Qaysi lavozimga")
        await self.send(CANDIDATE, text="7 000 000")
        sent = await self.press(CANDIDATE, "rf:start_availability:0")
        self.assertSent(sent, "Lavozim: <b>Rahbariyat</b>")
        self.assertNotSent(sent, "Bo'lim:")
        # "back" from the salary returns to the department
        await self.press(CANDIDATE, "rf:preview:edit")
        await self.press(CANDIDATE, "rf:preview:e_position")
        self.assertEqual((await db.get_draft(CANDIDATE))[1], "department")

        await self.press(CANDIDATE, f"rf:department:{await self.department_id('Rahbariyat')}")
        await self.send(CANDIDATE, text="7 000 000")
        await self.press(CANDIDATE, "rf:start_availability:0")
        await self.press(CANDIDATE, "rf:preview:send")
        app = await db.get_application_details(1)
        self.assertEqual((app["position_id"], app["position"]), (None, "Rahbariyat"))

    async def test_changing_the_department_clears_the_chosen_position(self):
        await self.add_positions("Savdo", "Sotuvchi")
        await self.upload_until_department()
        await self.press(CANDIDATE, f"rf:department:{await self.department_id('Savdo')}")
        await self.press(CANDIDATE, f"rf:position:{await self.position_id('Savdo', 'Sotuvchi')}")
        await self.press(CANDIDATE, "rf:expected_salary:<")
        await self.press(CANDIDATE, "rf:position:<")
        await self.press(CANDIDATE, f"rf:department:{await self.department_id('Ishlab chiqarish')}")
        data = (await db.get_draft(CANDIDATE))[2]
        self.assertEqual((data.get("position_id"), data["position"]), (None, "Ishlab chiqarish"))

    async def test_form_saved_before_positions_asks_the_department(self):
        await self.upload_until_department()
        # the old version stored "position" as the question key of the department
        await self.query("UPDATE application_drafts SET step = 'position' WHERE user_id = ?", CANDIDATE)
        self.assertSent(await self.send(CANDIDATE, text="/start"), "rs:continue")
        sent = await self.press(CANDIDATE, "rs:continue")
        self.assertSent(sent, "Qaysi bo'limga ariza", f"rf:department:{await self.department_id('Savdo')}")
        # an old "position = department" button is stale now
        self.assertSent(await self.press(CANDIDATE, f"rf:position:{await self.department_id('Savdo')}"),
                        "eskirgan")


# =====================================================================
# Admin: "Bo'limni o'zgartirish"
# =====================================================================
class TransferTests(PositionTestCase):
    async def test_admin_sets_department_and_position(self):
        await self.add_positions("Ishlab chiqarish", "Haydovchi", "Pishiriqchi")
        await self.make_employee(7201, "Haydovchi Xodim", "Haydovchi")
        await self.make_employee(7202, "Zavod Xodim", "Ishlab chiqarish")

        sent = await self.send(OWNER, text="🏢 Bo'limni o'zgartirish")
        self.assertSent(sent, "job:7201", "⚠️ Zavod Xodim — Ishlab chiqarish · lavozimsiz", "lavozimi belgilanmagan")
        self.assertNotSent(sent, "⚠️ Haydovchi Xodim")  # its department has no positions

        sent = await self.press(OWNER, "job:7201")
        ishlab = await self.department_id("Ishlab chiqarish")
        self.assertSent(sent, "Bo'lim: Haydovchi", f"jobd:7201:{ishlab}", "resetdept:7201")
        haydovchi_pos = await self.position_id("Ishlab chiqarish", "Haydovchi")
        self.assertSent(await self.press(OWNER, f"jobd:7201:{ishlab}"), f"jobp:7201:{haydovchi_pos}")
        sent = await self.press(OWNER, f"jobp:7201:{haydovchi_pos}")
        self.assertSent(sent, "Ishlab chiqarish · Haydovchi", "Avval: Haydovchi")
        job = await db.get_employee_job(7201)
        self.assertEqual(job[2:5], ("Ishlab chiqarish", haydovchi_pos, "Haydovchi"))
        self.assertEqual(await self.query("SELECT department FROM employees WHERE user_id = 7201"),
                         [("Ishlab chiqarish",)])

        # the old department is empty now and can be deactivated
        self.assertEqual((await db.department_usage(await self.department_id("Haydovchi")))[0], 0)

        # the employee keeps working: Zavod is chosen automatically
        sent = await self.send(7201, text="/start")
        self.assertSent(sent, "dumaloq video")

    async def test_move_to_savdo_asks_the_shop_and_department_without_positions_is_saved_at_once(self):
        await self.make_employee(7203, "Xodim", "Haydovchi")
        sent = await self.press(OWNER, f"jobd:7203:{await self.department_id('Savdo')}")
        self.assertSent(sent, "Savdo filiallari: xodim /start bosganda filialni tanlaydi")
        self.assertEqual((await db.get_employee_job(7203))[2], "Savdo")
        self.assertSent(await self.send(7203, text="/start"), "filialingizni tanlang")

    async def test_reset_lets_the_employee_choose_again(self):
        await self.add_positions("Savdo", "Sotuvchi")
        await self.make_employee(7204, "Xodim", "Savdo")
        self.assertSent(await self.press(OWNER, "resetdept:7204"), "bo'lim va lavozim tozalandi")
        sent = await self.send(7204, text="/start")
        self.assertSent(sent, "Qaysi bo'limda ishlaysiz")

    async def test_forged_and_stale_buttons(self):
        await self.make_employee(7205, "Xodim", "Savdo")
        self.assertSent(await self.press(OWNER, "jobp:7205:999"), "topilmadi")
        self.assertSent(await self.press(OWNER, "jobd:7205:999"), "topilmadi")
        self.assertSent(await self.press(OWNER, "job:abc"), "topilmadi")
        # employees cannot use it
        await self.press(7205, f"jobd:7205:{await self.department_id('Rahbariyat')}")
        self.assertEqual((await db.get_employee_job(7205))[2], "Savdo")


# =====================================================================
# Savdo branches and the base work place (Zavod)
# =====================================================================
class BaseWorkPlaceTests(PositionTestCase):
    async def test_zavod_is_the_base_and_not_a_branch(self):
        zavod = await self.location_id("Zavod")
        self.assertEqual((await db.get_base_location())[0], zavod)
        self.assertEqual(await self.query("SELECT COUNT(*) FROM department_locations WHERE location_id = ?", zavod),
                         [(0,)], "the base needs no links")
        self.assertNotIn(zavod, [row[0] for row in await db.list_locations(branches_only=True)])
        await db.init_db()  # repeating the migration changes nothing
        self.assertEqual((await db.get_base_location())[0], zavod)

        sent = await self.send(OWNER, text="/filiallar")
        self.assertSent(sent, "Savdo filiallari: 3 faol", "Zavod: ✅", "ct:base")
        sent = await self.press(OWNER, "ct:base")
        self.assertSent(sent, "asosiy ish joyi", "Ishlab chiqarish", "Rahbariyat", f"ct:lpoint:{zavod}",
                        '["◀️ Orqaga", "ct:home"]')
        self.assertNotSent(sent, "Savdo,")
        self.assertNotSent(await self.press(OWNER, "ct:locs"), f"ct:loc:{zavod}")

    async def test_moving_to_rahbariyat_never_asks_a_branch(self):
        # the screenshot: Savdo -> Rahbariyat must say Zavod, not "choose a branch"
        await self.add_positions("Savdo", "Sotuvchi")
        await self.make_employee(7301, "Xodim", "Savdo")
        sent = await self.press(OWNER, f"jobd:7301:{await self.department_id('Rahbariyat')}")
        self.assertSent(sent, "Ish joyi: Zavod (avtomatik)")
        self.assertNotSent(sent, "filialni tanlaydi")
        sent = await self.send(7301, text="/start")
        self.assertNotSent(sent, "filialingizni tanlang")
        self.assertSent(sent, "dumaloq video")
        sent = await self.send(7301, **self.VIDEO)
        self.assertEqual(len(self.sent_to(sent, ZAVOD_GROUP, "video")), 1)

    async def test_department_with_only_deactivated_shops_does_not_move_to_zavod(self):
        await self.make_employee(7302, "Savdo Xodim", "Savdo")
        for location_id, *_ in await db.list_locations(branches_only=True):
            await db.set_location_active(location_id, False)
        sent = await self.send(7302, text="/start")
        self.assertSent(sent, "ish joyi biriktirilmagan")
        self.assertNotSent(sent, "dumaloq video")


class NewInstallBaseTests(PositionTestCase):
    seed_legacy = False

    async def test_admin_enters_zavod_once(self):
        with mock.patch.multiple("config", LEGACY_GROUP_ID=None, LEGACY_PRODUCTION_LAT=None,
                                 LEGACY_PRODUCTION_LON=None, LEGACY_PRODUCTION_GROUP_ID=None):
            await db.init_db()
        await db.create_department("Ishlab chiqarish", [])
        await self.make_employee(7401, "Zavod Xodim", "Ishlab chiqarish")
        self.assertSent(await self.send(7401, text="/start"), "ish joyi biriktirilmagan")

        await self.bot_added_to_group(-100999, "Zavod guruhi")
        self.assertSent(await self.press(OWNER, "ct:base"), "kiritilmagan", "ct:newbase")
        self.assertSent(await self.press(OWNER, "ct:newbase"), "Zavod</b> joylashuvini yuboring")
        await self.send(OWNER, text="38.800000, 65.700000")
        self.assertSent(await self.press(OWNER, "ct:ptok"), "ct:g:-100999")
        self.assertSent(await self.press(OWNER, "ct:g:-100999"), "asosiy ish joyi", "Ishlab chiqarish")
        self.assertIsNotNone(await db.get_base_location())
        self.assertSent(await self.press(OWNER, "ct:newbase"), "allaqachon")

        self.assertSent(await self.send(7401, text="/start"), "dumaloq video")


# =====================================================================
# The owner cannot remove themself
# =====================================================================
class OwnerSelfRemovalTests(PositionTestCase):
    async def test_owner_is_not_listed_and_cannot_be_removed(self):
        await self.make_employee(OWNER, "Ega Xodim", "Savdo")
        await self.make_employee(7101, "Oddiy Xodim", "Savdo")

        sent = await self.send(OWNER, text="/hodimlar")
        self.assertSent(sent, "emp_7101", "1 ta")
        self.assertNotSent(sent, f"emp_{OWNER}")

        # old or forged buttons are refused too
        self.assertSent(await self.press(OWNER, f"emp_{OWNER}"), "ishdan chiqara olmaysiz")
        self.assertSent(await self.press(OWNER, f"empdel_{OWNER}"), "ishdan chiqara olmaysiz")
        self.assertEqual(await self.query("SELECT is_active FROM employees WHERE user_id = ?", OWNER), [(1,)])

        # other employees are removed as before
        await self.press(OWNER, "empdel_7101")
        self.assertEqual(await self.query("SELECT is_active FROM employees WHERE user_id = 7101"), [(0,)])


# =====================================================================
# Group cards name the position
# =====================================================================
class GroupCardPositionTests(PositionTestCase):
    async def test_check_in_out_and_auto_close_cards_show_the_position(self):
        rahbariyat = await self.department_id("Rahbariyat")
        await self.add_positions("Rahbariyat", "Menejer")
        await self.make_employee(7301, "Rahbar Xodim", "Rahbariyat")
        await db.set_employee_job(7301, rahbariyat, await self.position_id("Rahbariyat", "Menejer"))
        await self.send(7301, text="/start")

        sent = await self.check_in(7301, ZAVOD_POINT)
        self.assertSent(self.sent_to(sent, ZAVOD_GROUP), "Bo'lim: Rahbariyat", "Lavozim: Menejer",
                        "Filial: Zavod", "Keldi")
        sent = await self.check_out(7301, ZAVOD_POINT)
        self.assertSent(self.sent_to(sent, ZAVOD_GROUP), "Lavozim: Menejer", "Ketdi")

        await self.query("UPDATE attendance SET check_out_time = NULL, checkout_requested_at = '2026-01-01 10:00:00'")
        start = len(self.log)
        await auto_close._check_and_close(self.bot)
        self.assertSent(self.sent_to(self.log[start:], ZAVOD_GROUP), "Avtomatik yopildi", "Lavozim: Menejer")

    async def test_an_employee_without_a_position_gets_no_position_line(self):
        await self.make_employee(7302, "Zavod Xodim", "Ishlab chiqarish")
        await self.send(7302, text="/start")
        sent = self.sent_to(await self.check_in(7302, ZAVOD_POINT), ZAVOD_GROUP)
        self.assertSent(sent, "Bo'lim: Ishlab chiqarish", "Filial: Zavod")
        self.assertNotSent(sent, "Lavozim")
