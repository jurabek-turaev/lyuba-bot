import json
import logging
from datetime import date, datetime, timedelta

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = "data/attendance.db"


async def _column_exists(db, table: str, column: str) -> bool:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in await cursor.fetchall()]
    return column in columns


async def _convert_hourly_rates(db):
    """
    One-time conversion, run when the monthly_salary column is created.

    Hourly rates saved before the switch to monthly salaries become monthly
    salaries, so the hourly rate derived from them stays exactly the same:
    monthly salary = hourly rate * daily norm * standard month days.
    """
    # Imported here so database.py has no module-level dependency on services
    from services.worktime import STANDARD_MONTH_DAYS, shift_hours

    cursor = await db.execute(
        """SELECT user_id, shift_start, shift_end, hourly_rate FROM employees
           WHERE hourly_rate IS NOT NULL AND shift_start IS NOT NULL AND shift_end IS NOT NULL"""
    )
    for user_id, shift_start, shift_end, rate in await cursor.fetchall():
        monthly_salary = round(rate * shift_hours(shift_start, shift_end) * STANDARD_MONTH_DAYS)
        await db.execute(
            "UPDATE employees SET monthly_salary = ? WHERE user_id = ?",
            (monthly_salary, user_id),
        )


async def init_db():
    """Bot ishga tushganda bir marta chaqiriladi (bot.py da)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                username TEXT,
                department TEXT,
                is_active INTEGER NOT NULL DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                full_name TEXT NOT NULL,
                branch TEXT NOT NULL,
                work_date TEXT NOT NULL,
                check_in_time TEXT,
                check_in_distance INTEGER,
                check_out_time TEXT,
                check_out_distance INTEGER,
                work_hours REAL,
                auto_closed INTEGER DEFAULT 0,
                checkout_requested_at TEXT,
                archived INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Applications of new or removed employees waiting for the admin's decision
        await db.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                full_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                username TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                decided_at TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_applications_user ON applications (user_id)")

        # --- Migratsiya: eski bazaga yangi ustunlarni qo'shish (buzmasdan) ---
        # Botning oldingi versiyasidan qolgan baza uchun kerak.
        if not await _column_exists(db, "employees", "is_active"):
            await db.execute("ALTER TABLE employees ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if not await _column_exists(db, "attendance", "archived"):
            await db.execute("ALTER TABLE attendance ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        # Work schedule and pay settings (set by the admin via /ish_vaqti_belgilash)
        if not await _column_exists(db, "employees", "shift_start"):
            await db.execute("ALTER TABLE employees ADD COLUMN shift_start TEXT")
        if not await _column_exists(db, "employees", "shift_end"):
            await db.execute("ALTER TABLE employees ADD COLUMN shift_end TEXT")
        # hourly_rate is legacy: it is no longer written, only read once by the conversion below
        if not await _column_exists(db, "employees", "hourly_rate"):
            await db.execute("ALTER TABLE employees ADD COLUMN hourly_rate INTEGER")
        if not await _column_exists(db, "employees", "monthly_salary"):
            await db.execute("ALTER TABLE employees ADD COLUMN monthly_salary INTEGER")
            await _convert_hourly_rates(db)
        # Last Savdo shop chosen by the employee: FSM data lives in memory, so after
        # a bot restart the branch is restored from here
        if not await _column_exists(db, "employees", "last_branch"):
            await db.execute("ALTER TABLE employees ADD COLUMN last_branch TEXT")
        # Hiring flow: resumes, interviews, unfinished forms
        await _migrate_hr(db)

        await db.commit()

    await _migrate_catalog()


def today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


# ===========================
# Xodim (employee) funksiyalari
# ===========================
async def get_employee_name(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT full_name FROM employees WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def save_employee_name(user_id: int, full_name: str, username: str = None):
    """
    Ism-familiya (va username) saqlanadi/yangilanadi. UPSERT ishlatiladi —
    shunda mavjud 'department' qiymati o'chib ketmaydi.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO employees (user_id, full_name, username) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   full_name = excluded.full_name,
                   username = excluded.username""",
            (user_id, full_name, username),
        )
        await db.commit()


async def get_employee_placement(user_id: int):
    """(department_id, location_id) of the employee, or None when there is no row."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT department_id, location_id FROM employees WHERE user_id = ?", (user_id,)
        )
        return await cursor.fetchone()


async def set_employee_department(user_id: int, department_id: int):
    """
    Bo'lim — bir marta, ro'yxatdan o'tishda saqlanadi. UPSERT — ism hali kiritilmagan
    bo'lsa ham ishlaydi. The department name is copied into the old text column too:
    the tabel and the shift flow still read it.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO employees (user_id, department, department_id)
               VALUES (?, (SELECT name FROM departments WHERE id = ?), ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   department = excluded.department,
                   department_id = excluded.department_id""",
            (user_id, department_id, department_id),
        )
        await db.commit()


async def set_employee_job(user_id: int, department_id: int, position_id: int | None):
    """
    Department and position together (the employee's own choice at /start, or the
    admin's transfer). A chosen location of another department does not survive
    the change; the same department keeps it.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO employees (user_id, department, department_id, position_id)
               VALUES (?, (SELECT name FROM departments WHERE id = ?), ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   location_id = CASE
                       WHEN employees.department_id IS excluded.department_id
                       THEN employees.location_id ELSE NULL END,
                   department = excluded.department,
                   department_id = excluded.department_id,
                   position_id = excluded.position_id""",
            (user_id, department_id, department_id, position_id),
        )
        await db.commit()


async def get_employee_job(user_id: int):
    """(full_name, department_id, department, position_id, position, is_active) or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT e.full_name, e.department_id, d.name, e.position_id, p.name, e.is_active
               FROM employees e
               LEFT JOIN departments d ON d.id = e.department_id
               LEFT JOIN positions p ON p.id = e.position_id
               WHERE e.user_id = ?""",
            (user_id,),
        )
        return await cursor.fetchone()


async def reset_employee_department(user_id: int):
    """
    Xodimning bo'limini (va lavozimini) tozalaydi — shundan keyin u /start bosganda
    bo'lim va lavozim tanlash savoli yana beriladi. Faqat OWNER_ID chaqira oladi.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE employees SET department = NULL, department_id = NULL, position_id = NULL
               WHERE user_id = ?""",
            (user_id,),
        )
        await db.commit()


async def set_employee_location(user_id: int, location_id: int):
    """
    Remember the location the employee chose (restored after a bot restart).
    The name is copied into the old last_branch text column.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE employees
               SET location_id = ?, last_branch = (SELECT name FROM locations WHERE id = ?)
               WHERE user_id = ?""",
            (location_id, location_id, user_id),
        )
        await db.commit()


async def update_username(user_id: int, username: str):
    """Har safar video kelganda username'ni yangilab turadi (xodim o'zgartirgan bo'lishi mumkin)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE employees SET username = ? WHERE user_id = ?",
            (username, user_id),
        )
        await db.commit()


# ===========================
# Davomat (attendance) funksiyalari
# ===========================
async def get_open_attendance_id(user_id: int):
    """
    Foydalanuvchining hali 'Ketdim' bosilmagan so'nggi yozuvini topadi.
    Sana bo'yicha filtr YO'Q — tungi smenada Keldim/Ketdim turli
    kalendar kunlariga to'g'ri kelishi mumkin.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT id FROM attendance
               WHERE user_id = ? AND check_out_time IS NULL
               ORDER BY id DESC LIMIT 1""",
            (user_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def has_closed_attendance_today(user_id: int, work_date: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT id FROM attendance
               WHERE user_id = ? AND work_date = ? AND check_out_time IS NOT NULL""",
            (user_id, work_date),
        )
        return (await cursor.fetchone()) is not None


async def create_check_in(user_id: int, full_name: str, branch: str, distance: int,
                          work_date: str, location_id: int):
    """branch is the location name at check-in time, kept as history; location_id is the link."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO attendance
               (user_id, full_name, branch, work_date, check_in_time, check_in_distance, location_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, full_name, branch, work_date, now, distance, location_id),
        )
        await db.commit()


async def create_check_out(attendance_id: int, distance: int):
    """
    Close the record. Returns the check-out time, or None when the record is
    already closed (for example auto-close closed it while the employee was
    still sending the location) — a closed record is never overwritten.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """UPDATE attendance
               SET check_out_time = ?, check_out_distance = ?
               WHERE id = ? AND check_out_time IS NULL""",
            (now, distance, attendance_id),
        )
        await db.commit()
    return now if cursor.rowcount == 1 else None


async def save_work_hours(attendance_id: int, hours: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE attendance SET work_hours = ? WHERE id = ?",
            (hours, attendance_id),
        )
        await db.commit()


async def get_attendance_times(attendance_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT check_in_time, check_out_time FROM attendance WHERE id = ?",
            (attendance_id,),
        )
        return await cursor.fetchone()


async def get_all_open_attendance():
    """
    Hali 'Ketdim' bosilmagan barcha yozuvlarni qaytaradi — auto-close tekshiruvi uchun.
    The last columns are the group of the record's location (None when it has no group)
    and the employee's department.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT a.id, a.user_id, a.full_name, a.branch, a.work_date, a.check_in_time,
                      a.checkout_requested_at, l.group_id, e.department
               FROM attendance a
               LEFT JOIN locations l ON l.id = a.location_id
               LEFT JOIN employees e ON e.user_id = a.user_id
               WHERE a.check_out_time IS NULL"""
        )
        return await cursor.fetchall()


async def mark_checkout_requested(attendance_id: int):
    """
    Xodim allaqachon ochiq smenasi bo'la turib qayta video yuborganda chaqiriladi —
    bu 'ketish niyati' bildirilgan vaqt sifatida belgilanadi. Auto-close shu
    vaqtdan 15 daqiqa o'tsa, aynan shu vaqtni chiqish vaqti sifatida yozadi.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE attendance SET checkout_requested_at = ? WHERE id = ?",
            (now, attendance_id),
        )
        await db.commit()


async def auto_close_attendance(attendance_id: int, check_out_time_str: str):
    """
    Xodim 'Ketdim' bosmagan, lekin filial yopilish vaqtidan 15 daqiqa
    o'tgan yozuvni tizim tomonidan yopish. check_out_distance qasddan
    NULL qoldiriladi — chunki lokatsiya haqiqatda tasdiqlanmagan.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE attendance
               SET check_out_time = ?, check_out_distance = NULL, auto_closed = 1
               WHERE id = ?""",
            (check_out_time_str, attendance_id),
        )
        await db.commit()


async def get_month_attendance(year: int, month: int):
    """
    Berilgan oy uchun barcha davomat yozuvlarini xodim ma'lumotlari (jumladan
    lavozim/bo'lim) bilan birga qaytaradi — services/excel.py shundan tabel yasaydi.

    Arxivlangan (archived=1) yozuvlar ham kiritiladi, chunki bu funksiya oylik
    arxivlash jarayonida oy TO'LIQ arxivlanishidan oldin ham, keyin ham chaqirilishi
    mumkin. Kundalik /tabel joriy (arxivlanmagan) oyni so'raydi — u yozuvlar baribir
    archived=0 bo'ladi, shuning uchun natija bir xil.

    Branch column: rows written before departments/locations moved to the database
    stored the factory DEPARTMENT name as the branch ("Haydovchi"). The migration
    linked them to their location ("Zavod"); the tabel shows that location, so the
    same work place is not listed twice ("Haydovchi, Zavod"). The old factory
    department names count too, also after a rename ("I.T.R xodimlari" -> "Rahbariyat").
    """
    month_prefix = f"{year:04d}-{month:02d}"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT a.user_id, a.full_name, e.username, e.department, p.name,
                      CASE WHEN l.name IS NOT NULL AND (a.branch IN (SELECT name FROM departments)
                                                         OR a.branch IN (?, ?, ?))
                           THEN l.name ELSE a.branch END,
                      a.work_date, a.work_hours, a.check_in_time
               FROM attendance a
               LEFT JOIN employees e ON e.user_id = a.user_id
               LEFT JOIN positions p ON p.id = e.position_id
               LEFT JOIN locations l ON l.id = a.location_id
               WHERE a.work_date LIKE ?
               ORDER BY a.full_name, a.work_date""",
            (*LEGACY_FACTORY_DEPARTMENTS, f"{month_prefix}%"),
        )
        return await cursor.fetchall()


# ===========================
# Xodimlarni boshqarish (faollik / soft-delete)
# ===========================
async def is_employee_active(user_id: int) -> bool:
    """
    Xodim faolmi? Ro'yxatda yo'q (yangi) bo'lsa ham faol hisoblanadi —
    faqat qasddan o'chirilgan (is_active=0) xodimlargina bloklanadi.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT is_active FROM employees WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return True  # hali ro'yxatdan o'tmagan — botga kirishiga ruxsat
        return row[0] == 1


async def get_active_employees():
    """Faol (is_active=1) xodimlar ro'yxati — /hodimlar uchun."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT user_id, full_name, department
               FROM employees
               WHERE is_active = 1
               ORDER BY full_name"""
        )
        return await cursor.fetchall()


async def get_active_employees_with_job():
    """Active employees with their department and position names: (user_id, full_name, department, position)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT e.user_id, e.full_name, COALESCE(d.name, e.department), p.name
               FROM employees e
               LEFT JOIN departments d ON d.id = e.department_id
               LEFT JOIN positions p ON p.id = e.position_id
               WHERE e.is_active = 1
               ORDER BY e.full_name"""
        )
        return await cursor.fetchall()


async def get_employee_short(user_id: int):
    """Bitta xodimning qisqa ma'lumoti (tasdiqlash oynasi uchun)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT full_name, department, is_active FROM employees WHERE user_id = ?",
            (user_id,),
        )
        return await cursor.fetchone()


async def deactivate_employee(user_id: int):
    """
    Xodimni 'ishdan ketgan' deb belgilaydi (soft-delete). Ma'lumotlari va
    davomat tarixi O'CHIRILMAYDI — faqat botga kirishi bloklanadi va u
    xodimlar ro'yxatidan chiqadi. Faqat OWNER_ID chaqiradi.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE employees SET is_active = 0 WHERE user_id = ?", (user_id,)
        )
        await db.commit()


# ===========================
# Oylik arxivlash
# ===========================
async def count_unarchived_in_month(year: int, month: int) -> int:
    """Berilgan oyda hali arxivlanmagan yozuvlar soni — ikki marta arxivlamaslik uchun."""
    month_prefix = f"{year:04d}-{month:02d}"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM attendance WHERE work_date LIKE ? AND archived = 0",
            (f"{month_prefix}%",),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def archive_month(year: int, month: int) -> int:
    """
    Berilgan oyning barcha yozuvlarini archived=1 qiladi (o'CHIRMAYDI).
    Arxivlangan yozuvlar soni qaytariladi.
    """
    month_prefix = f"{year:04d}-{month:02d}"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE attendance SET archived = 1 WHERE work_date LIKE ? AND archived = 0",
            (f"{month_prefix}%",),
        )
        await db.commit()
        return cursor.rowcount


# ===========================
# Work schedule and pay settings
# ===========================
async def get_departments():
    """Names of all departments that have at least one active employee."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT DISTINCT department FROM employees
               WHERE is_active = 1 AND department IS NOT NULL AND department != ''
               ORDER BY department"""
        )
        return [row[0] for row in await cursor.fetchall()]


async def get_active_employees_by_department(department: str):
    """Active employees of one department: (user_id, full_name, shift_start, shift_end)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT user_id, full_name, shift_start, shift_end
               FROM employees
               WHERE is_active = 1 AND department = ?
               ORDER BY full_name""",
            (department,),
        )
        return await cursor.fetchall()


async def save_work_schedule(user_id: int, shift_start: str, shift_end: str, monthly_salary: int):
    """
    Save the employee's permanent daily schedule ("HH:MM" strings) and fixed monthly salary.
    Overwrites any previous values. The hourly rate is not stored: it is derived from
    the monthly salary and the daily norm, so it follows every schedule change.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE employees
               SET shift_start = ?, shift_end = ?, monthly_salary = ?
               WHERE user_id = ?""",
            (shift_start, shift_end, monthly_salary, user_id),
        )
        await db.commit()


async def get_pay_settings():
    """
    Schedule and fixed monthly salary of every employee that has a schedule.
    Inactive employees are included on purpose: their last month still has to be paid.
    Returns {user_id: (shift_start, shift_end, monthly_salary)}.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT user_id, shift_start, shift_end, monthly_salary
               FROM employees
               WHERE shift_start IS NOT NULL AND shift_end IS NOT NULL"""
        )
        rows = await cursor.fetchall()
    return {user_id: (start, end, salary) for user_id, start, end, salary in rows}


# ===========================
# Applications (hiring flow)
# ===========================
# Statuses. An application is "active" while it waits for the admin.
STATUS_PENDING = "pending"      # waiting for the admin's review
STATUS_INVITED = "invited"      # invited to an interview (the time may be moved)
STATUS_APPROVED = "approved"    # hired (also: approved in the old name+phone flow)
STATUS_REJECTED = "rejected"
STATUS_NO_SHOW = "no_show"      # did not come to the interview
STATUS_BLOCKED = "blocked"
STATUS_UNBLOCKED = "unblocked"  # a lifted block: the user may apply again at once
STATUS_CANCELLED = "cancelled"  # withdrawn by the candidate
ACTIVE_STATUSES = (STATUS_PENDING, STATUS_INVITED)
# Closed without hiring: personal data of these is deleted after the retention period
PURGEABLE_STATUSES = (STATUS_REJECTED, STATUS_NO_SHOW, STATUS_UNBLOCKED, STATUS_CANCELLED)

# Kinds. Rows created before the hiring flow have kind NULL and are read as "legacy".
KIND_LEGACY = "legacy"  # old flow: name + phone only
KIND_FORM = "form"      # resume built in the bot (PDF generated by the bot)
KIND_UPLOAD = "upload"  # the candidate's own resume file

# Columns added to the old applications table (all nullable: old rows stay valid)
_HR_COLUMNS = [
    ("kind", "TEXT"),
    ("department_id", "INTEGER"),        # chosen department
    ("position", "TEXT"),                # the position's name at the moment of applying (history);
                                         # before positions existed: the department's name
    ("position_id", "INTEGER"),          # chosen position (NULL: department without positions / old rows)
    ("expected_salary", "INTEGER"),      # so'm per month
    ("start_availability", "TEXT"),
    ("resume_data", "TEXT"),             # JSON of the form answers (kind "form")
    ("resume_file_id", "TEXT"),          # Telegram file_id of the PDF / uploaded file
    ("resume_file_name", "TEXT"),
    ("resume_file_type", "TEXT"),        # "document" or "photo"
    ("card_message_id", "INTEGER"),      # the admin's card, edited as the status changes
    ("interview_at", "TEXT"),            # "YYYY-MM-DD HH:MM"
    ("interview_place", "TEXT"),
    ("interview_location_id", "INTEGER"),
    ("interview_note", "TEXT"),
    ("invited_at", "TEXT"),
    ("reminder_sent_at", "TEXT"),
    ("result_asked_at", "TEXT"),         # the admin was asked "how did the interview go?"
    ("reschedule_request", "TEXT"),      # the candidate's wish when asking for another time
    ("reschedule_requested_at", "TEXT"),
    ("reject_reason", "TEXT"),           # internal only, never shown to the candidate
]

_APPLICATION_SELECT = """
    SELECT id, user_id, full_name, phone, username, status, created_at, decided_at,
           COALESCE(kind, 'legacy') AS kind, department_id, position, position_id,
           (SELECT name FROM departments WHERE id = applications.department_id) AS department,
           expected_salary,
           start_availability, resume_data, resume_file_id, resume_file_name, resume_file_type,
           card_message_id, interview_at, interview_place, interview_location_id, interview_note,
           invited_at, reminder_sent_at, result_asked_at, reschedule_request, reschedule_requested_at,
           reject_reason
    FROM applications
"""


async def _migrate_hr(db):
    """Columns and tables of the hiring flow; safe to repeat, old rows are not changed."""
    for column, sql_type in _HR_COLUMNS:
        if not await _column_exists(db, "applications", column):
            await db.execute(f"ALTER TABLE applications ADD COLUMN {column} {sql_type}")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_applications_status ON applications (status)")
    # Unfinished forms: kept in the database because FSM memory is lost on a restart
    await db.execute("""
        CREATE TABLE IF NOT EXISTS application_drafts (
            user_id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            step TEXT,
            data TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        )
    """)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _application_dict(cursor, row) -> dict:
    app = {column[0]: value for column, value in zip(cursor.description, row)}
    app["resume_data"] = json.loads(app["resume_data"]) if app["resume_data"] else None
    return app


async def is_employee_approved(user_id: int) -> bool:
    """
    True for an active employee who completed registration: the employee has a
    name, or already has attendance records (safety net for old data).
    A row that only holds a department (registration never finished) is NOT approved.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT 1 FROM employees e
               WHERE e.user_id = ? AND e.is_active = 1
                 AND (
                     (e.full_name IS NOT NULL AND TRIM(e.full_name) != '')
                     OR EXISTS (SELECT 1 FROM attendance a WHERE a.user_id = e.user_id)
                 )""",
            (user_id,),
        )
        return (await cursor.fetchone()) is not None


# ---------- drafts (unfinished forms) ----------
async def get_draft(user_id: int):
    """(kind, step, data, updated_at) of the user's unfinished form, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT kind, step, data, updated_at FROM application_drafts WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    kind, step, data, updated_at = row
    return kind, step, json.loads(data), updated_at


async def save_draft(user_id: int, kind: str, step: str | None, data: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO application_drafts (user_id, kind, step, data, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   kind = excluded.kind, step = excluded.step,
                   data = excluded.data, updated_at = excluded.updated_at""",
            (user_id, kind, step, json.dumps(data, ensure_ascii=False), _now_str()),
        )
        await db.commit()


async def delete_draft(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM application_drafts WHERE user_id = ?", (user_id,))
        await db.commit()


# ---------- creating applications ----------
_NO_ACTIVE_APPLICATION = """
    NOT EXISTS (SELECT 1 FROM applications WHERE user_id = ? AND status IN ('pending', 'invited'))
"""


async def create_application(user_id: int, full_name: str, phone: str, username: str | None):
    """
    Old flow (name + phone). Store a new pending application and return its id.
    Returns None when the user already has an active one (double tap, repeated request).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            f"""INSERT INTO applications (user_id, full_name, phone, username, status, created_at, kind)
                SELECT ?, ?, ?, ?, 'pending', ?, 'legacy'
                WHERE {_NO_ACTIVE_APPLICATION}""",
            (user_id, full_name, phone, username, _now_str(), user_id),
        )
        await db.commit()
        return cursor.lastrowid if cursor.rowcount == 1 else None


async def submit_application(
    user_id: int,
    username: str | None,
    *,
    kind: str,
    full_name: str,
    phone: str,
    department_id: int,
    expected_salary: int,
    position_id: int | None = None,
    start_availability: str | None = None,
    resume_data: dict | None = None,
    resume_file_id: str | None = None,
    resume_file_name: str | None = None,
    resume_file_type: str | None = None,
):
    """
    Turn a finished form / upload into a pending application and delete the draft,
    in ONE transaction. Returns the new id, or None when the user already has an
    active application (the draft is kept then).
    The position text is the position's name, or the department's name when the
    department has no positions (as before positions existed).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            f"""INSERT INTO applications (
                    user_id, full_name, phone, username, status, created_at, kind,
                    department_id, position_id, position, expected_salary, start_availability,
                    resume_data, resume_file_id, resume_file_name, resume_file_type)
                SELECT ?, ?, ?, ?, 'pending', ?, ?,
                       ?, ?, COALESCE((SELECT name FROM positions WHERE id = ?),
                                      (SELECT name FROM departments WHERE id = ?)), ?, ?,
                       ?, ?, ?, ?
                WHERE {_NO_ACTIVE_APPLICATION}""",
            (
                user_id, full_name, phone, username, _now_str(), kind,
                department_id, position_id, position_id, department_id, expected_salary, start_availability,
                json.dumps(resume_data, ensure_ascii=False) if resume_data is not None else None,
                resume_file_id, resume_file_name, resume_file_type,
                user_id,
            ),
        )
        if cursor.rowcount != 1:
            return None
        app_id = cursor.lastrowid
        await db.execute("DELETE FROM application_drafts WHERE user_id = ?", (user_id,))
        await db.commit()
        return app_id


# ---------- reading applications ----------
async def get_latest_application(user_id: int):
    """(id, status, decided_at) of the user's most recent application, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT id, status, decided_at FROM applications
               WHERE user_id = ? ORDER BY id DESC LIMIT 1""",
            (user_id,),
        )
        return await cursor.fetchone()


async def get_application(app_id: int):
    """(id, user_id, full_name, phone, username, status, created_at) or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT id, user_id, full_name, phone, username, status, created_at
               FROM applications WHERE id = ?""",
            (app_id,),
        )
        return await cursor.fetchone()


async def get_application_details(app_id: int) -> dict | None:
    """Every column of the application as a dict (resume_data decoded), or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(_APPLICATION_SELECT + " WHERE id = ?", (app_id,))
        row = await cursor.fetchone()
        return _application_dict(cursor, row) if row else None


async def get_active_application(user_id: int) -> dict | None:
    """The user's pending / invited application as a dict, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            _APPLICATION_SELECT
            + " WHERE user_id = ? AND status IN ('pending', 'invited') ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
        return _application_dict(cursor, row) if row else None


async def list_applications(statuses, limit: int | None = None) -> list:
    """Applications with the given statuses as dicts, oldest first."""
    statuses = tuple(statuses)
    marks = ", ".join("?" * len(statuses))
    sql = _APPLICATION_SELECT + f" WHERE status IN ({marks}) ORDER BY id"
    params = list(statuses)
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(sql, params)
        return [_application_dict(cursor, row) for row in await cursor.fetchall()]


async def count_applications_by_status() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT status, COUNT(*) FROM applications GROUP BY status")
        return dict(await cursor.fetchall())


async def get_interviews_between(start: str, end: str) -> list:
    """Invited applications whose interview is in [start, end) ("YYYY-MM-DD HH:MM"), soonest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            _APPLICATION_SELECT
            + " WHERE status = 'invited' AND interview_at >= ? AND interview_at < ? ORDER BY interview_at",
            (start, end),
        )
        return [_application_dict(cursor, row) for row in await cursor.fetchall()]


async def get_pending_applications():
    """Pending applications, oldest first, same columns as get_application()."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT id, user_id, full_name, phone, username, status, created_at
               FROM applications WHERE status = 'pending' ORDER BY id"""
        )
        return await cursor.fetchall()


# ---------- changing applications ----------
# Every change is conditional on the current status, so a double tap or an old
# button can never move an application twice. Each returns True when it applied.
async def _update_application(sql: str, params: tuple) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(sql, params)
        await db.commit()
        return cursor.rowcount == 1


async def _update_application_many(sql: str, params: tuple) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(sql, params)
        await db.commit()
        return cursor.rowcount > 0


async def set_card_message(app_id: int, message_id: int):
    await _update_application(
        "UPDATE applications SET card_message_id = ? WHERE id = ?", (message_id, app_id)
    )


async def decide_application(app_id: int, status: str) -> bool:
    """Give an active application a final status ('rejected' or 'blocked')."""
    return await _update_application(
        """UPDATE applications SET status = ?, decided_at = ?
           WHERE id = ? AND status IN ('pending', 'invited')""",
        (status, _now_str(), app_id),
    )


async def reject_application(app_id: int, reason: str | None = None) -> bool:
    """Reject an active application; the reason is internal (statistics only)."""
    return await _update_application(
        """UPDATE applications SET status = 'rejected', decided_at = ?, reject_reason = ?
           WHERE id = ? AND status IN ('pending', 'invited')""",
        (_now_str(), reason, app_id),
    )


async def invite_to_interview(app_id: int, interview_at: str, place: str,
                              location_id: int | None = None, note: str | None = None) -> bool:
    """
    Invite to an interview, or move an invited one to a new time.
    The reminder and the candidate's reschedule request are reset.
    """
    return await _update_application(
        """UPDATE applications SET
               status = 'invited', interview_at = ?, interview_place = ?,
               interview_location_id = ?, interview_note = ?, invited_at = ?,
               reminder_sent_at = NULL, result_asked_at = NULL,
               reschedule_request = NULL, reschedule_requested_at = NULL
           WHERE id = ? AND status IN ('pending', 'invited')""",
        (interview_at, place, location_id, note, _now_str(), app_id),
    )


async def request_reschedule(app_id: int, wish: str) -> bool:
    """The candidate asks for another interview time."""
    return await _update_application(
        """UPDATE applications SET reschedule_request = ?, reschedule_requested_at = ?
           WHERE id = ? AND status = 'invited'""",
        (wish, _now_str(), app_id),
    )


async def mark_no_show(app_id: int) -> bool:
    return await _update_application(
        """UPDATE applications SET status = 'no_show', decided_at = ?
           WHERE id = ? AND status = 'invited'""",
        (_now_str(), app_id),
    )


async def cancel_application(app_id: int) -> bool:
    """The candidate withdraws an active application."""
    return await _update_application(
        """UPDATE applications SET status = 'cancelled', decided_at = ?
           WHERE id = ? AND status IN ('pending', 'invited')""",
        (_now_str(), app_id),
    )


async def mark_reminder_sent(app_id: int) -> bool:
    """False when the reminder was already sent (or the interview is not active)."""
    return await _update_application(
        """UPDATE applications SET reminder_sent_at = ?
           WHERE id = ? AND status = 'invited' AND reminder_sent_at IS NULL""",
        (_now_str(), app_id),
    )


async def mark_result_asked(app_id: int) -> bool:
    """False when the admin was already asked about this interview."""
    return await _update_application(
        """UPDATE applications SET result_asked_at = ?
           WHERE id = ? AND status = 'invited' AND result_asked_at IS NULL""",
        (_now_str(), app_id),
    )


async def get_interviews_without_result(now: str) -> list:
    """Invited applications whose interview time ("YYYY-MM-DD HH:MM") has passed and the admin was not asked yet."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            _APPLICATION_SELECT
            + " WHERE status = 'invited' AND interview_at <= ? AND result_asked_at IS NULL ORDER BY interview_at",
            (now,),
        )
        return [_application_dict(cursor, row) for row in await cursor.fetchall()]


async def approve_application(app_id: int):
    """
    Hire (approve) an active application and add (or re-activate) the employee in
    ONE transaction. Returns (user_id, full_name), or None when it was not active.

    The department and the position are the ones the candidate applied for. Old name+phone
    applications have no position: a returning employee keeps the department,
    a row that never had a name (unfinished registration) loses its leftover
    department, so the person chooses it again after the approval.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """UPDATE applications SET status = 'approved', decided_at = ?
               WHERE id = ? AND status IN ('pending', 'invited')""",
            (_now_str(), app_id),
        )
        if cursor.rowcount != 1:
            return None

        cursor = await db.execute(
            """SELECT a.user_id, a.full_name, a.username, d.id, d.name,
                      (SELECT p.id FROM positions p WHERE p.id = a.position_id AND p.department_id = d.id)
               FROM applications a LEFT JOIN departments d ON d.id = a.department_id
               WHERE a.id = ?""",
            (app_id,),
        )
        user_id, full_name, username, department_id, department, position_id = await cursor.fetchone()

        if department_id is None:
            await db.execute(
                """INSERT INTO employees (user_id, full_name, username, is_active) VALUES (?, ?, ?, 1)
                   ON CONFLICT(user_id) DO UPDATE SET
                       department = CASE
                           WHEN employees.full_name IS NULL OR TRIM(employees.full_name) = ''
                           THEN NULL ELSE employees.department END,
                       department_id = CASE
                           WHEN employees.full_name IS NULL OR TRIM(employees.full_name) = ''
                           THEN NULL ELSE employees.department_id END,
                       full_name = excluded.full_name,
                       username = excluded.username,
                       is_active = 1""",
                (user_id, full_name, username),
            )
        else:
            # A chosen location of another department must not survive the change
            await db.execute(
                """INSERT INTO employees (user_id, full_name, username, department, department_id,
                                         position_id, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, 1)
                   ON CONFLICT(user_id) DO UPDATE SET
                       location_id = CASE
                           WHEN employees.department_id IS excluded.department_id
                           THEN employees.location_id ELSE NULL END,
                       department = excluded.department,
                       department_id = excluded.department_id,
                       position_id = excluded.position_id,
                       full_name = excluded.full_name,
                       username = excluded.username,
                       is_active = 1""",
                (user_id, full_name, username, department, department_id, position_id),
            )
        await db.commit()
        return user_id, full_name


async def get_last_work_info(user_id: int):
    """(department, last work date) of a returning employee; None when the user never worked."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT e.department, MAX(a.work_date)
               FROM attendance a LEFT JOIN employees e ON e.user_id = a.user_id
               WHERE a.user_id = ?""",
            (user_id,),
        )
        department, last_work_date = await cursor.fetchone()
    return None if last_work_date is None else (department, last_work_date)


async def unblock_user(user_id: int) -> bool:
    """
    Lift a block: the blocked application becomes 'unblocked', so the user may
    apply again at once. False when the user was not blocked.
    """
    return await _update_application_many(
        """UPDATE applications SET status = 'unblocked', decided_at = ?
           WHERE user_id = ? AND status = 'blocked'""",
        (_now_str(), user_id),
    )


async def get_blocked_applicants():
    """(user_id, full_name, phone) of every user whose latest application is 'blocked'."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT a.user_id, a.full_name, a.phone FROM applications a
               WHERE a.status = 'blocked'
                 AND a.id = (SELECT MAX(id) FROM applications WHERE user_id = a.user_id)
               ORDER BY a.id"""
        )
        return await cursor.fetchall()


# ---------- data retention ----------
async def purge_expired_applications(application_days: int, draft_days: int):
    """
    Delete personal data that is no longer needed:
    - applications closed without hiring (rejected, no-show, unblocked, cancelled)
      more than application_days ago;
    - unfinished forms not touched for draft_days.
    Hired and blocked applications are kept. Returns (applications, drafts) deleted.
    """
    now = datetime.now()
    app_cutoff = (now - timedelta(days=application_days)).strftime("%Y-%m-%d %H:%M:%S")
    draft_cutoff = (now - timedelta(days=draft_days)).strftime("%Y-%m-%d %H:%M:%S")
    marks = ", ".join("?" * len(PURGEABLE_STATUSES))
    async with aiosqlite.connect(DB_PATH) as db:
        apps = await db.execute(
            f"DELETE FROM applications WHERE status IN ({marks}) AND decided_at < ?",
            (*PURGEABLE_STATUSES, app_cutoff),
        )
        drafts = await db.execute(
            "DELETE FROM application_drafts WHERE updated_at < ?", (draft_cutoff,)
        )
        await db.commit()
        return apps.rowcount, drafts.rowcount


# ===========================
# Catalog: departments, locations and Telegram groups
# (moved from config.py into the database, managed by the admin)
# ===========================
# Shared location created on the first migration for the factory departments
# (Ishlab chiqarish, Haydovchi, I.T.R xodimlari — now "Rahbariyat")
FACTORY_LOCATION_NAME = "Zavod"


async def _table_exists(db, table: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    )
    return (await cursor.fetchone()) is not None


async def _migrate_catalog():
    """
    Moves departments, locations and groups from config.py into the database.
    Called from init_db() on every start; safe to repeat. Runs in ONE transaction:
    if anything fails, nothing is changed.

    - Tables and id columns are created only when missing.
    - The catalog is filled from config.py ONCE, in the run that creates the tables.
    - Rows without an id yet are linked by name. Old text columns
      (employees.department, employees.last_branch, attendance.branch) are never changed.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN")
        first_run = not await _table_exists(db, "departments")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                group_id INTEGER,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS departments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        # One location: the employee gets it automatically.
        # Several locations: the employee chooses one (like the Savdo shops).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS department_locations (
                department_id INTEGER NOT NULL REFERENCES departments(id),
                location_id INTEGER NOT NULL REFERENCES locations(id),
                PRIMARY KEY (department_id, location_id)
            )
        """)
        # Real job titles inside a department (Savdo: sotuvchi, ofitsiant, ...), entered by
        # the admin. A department without positions works as before: no position question.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                department_id INTEGER NOT NULL REFERENCES departments(id),
                name TEXT NOT NULL COLLATE NOCASE,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE (department_id, name)
            )
        """)
        # Groups the bot is a member of. Filled automatically when the bot is added
        # to a group, so the admin picks a group from a list instead of typing its id.
        # ("groups" is an SQL keyword, hence tg_groups.)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tg_groups (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                is_member INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )
        """)

        if not await _column_exists(db, "employees", "department_id"):
            await db.execute("ALTER TABLE employees ADD COLUMN department_id INTEGER")
        # Last location the employee chose (needed for departments with several locations)
        if not await _column_exists(db, "employees", "location_id"):
            await db.execute("ALTER TABLE employees ADD COLUMN location_id INTEGER")
        # The employee's position (NULL: not chosen yet, or the department has no positions)
        if not await _column_exists(db, "employees", "position_id"):
            await db.execute("ALTER TABLE employees ADD COLUMN position_id INTEGER")
        # attendance.branch stays as the historical name; location_id is the stable link
        if not await _column_exists(db, "attendance", "location_id"):
            await db.execute("ALTER TABLE attendance ADD COLUMN location_id INTEGER")

        # "branch" = a Savdo shop the employee chooses; "base" = Zavod, the one main work
        # place of every department without shops (Ishlab chiqarish, Rahbariyat)
        if not await _column_exists(db, "locations", "kind"):
            await db.execute("ALTER TABLE locations ADD COLUMN kind TEXT NOT NULL DEFAULT 'branch'")

        if first_run:
            await _seed_catalog(db)
        await _link_rows_by_name(db)
        await _rename_itr_department(db)
        await _make_factory_the_base(db)

        await db.commit()


# What config.py held before the catalog moved into the database. Used ONCE, by the
# migration that creates the catalog tables, so an existing installation keeps
# working without the admin re-entering anything. Afterwards the admin manages it.
LEGACY_SHOPS = [  # department "Savdo": (name, lat, lon)
    ("Lyuba Orzu bozor", 38.828995, 65.793966),
    ("Eski bozor", 38.869932, 65.801341),
    ("Koson Lyuba", 39.035179, 65.583693),
]
LEGACY_SHOP_DEPARTMENT = "Savdo"
LEGACY_FACTORY_DEPARTMENTS = ["Ishlab chiqarish", "Haydovchi", "I.T.R xodimlari"]


async def _seed_catalog(db):
    """
    Fill the new, empty catalog with the old hardcoded setup.

    The groups and the factory coordinates come from the old .env values
    (GROUP_ID, PRODUCTION_LAT / PRODUCTION_LON / PRODUCTION_GROUP_ID):
    - GROUP_ID set              -> the Savdo shops are created;
    - all PRODUCTION_* set      -> "Zavod" and the factory departments are created;
    - nothing set (new install) -> the catalog stays empty, the admin fills it in the bot.
    """
    # Imported here so database.py has no module-level dependency on config
    from config import (LEGACY_GROUP_ID, LEGACY_PRODUCTION_GROUP_ID,
                        LEGACY_PRODUCTION_LAT, LEGACY_PRODUCTION_LON)

    now = _now_str()

    async def add_location(name: str, lat: float, lon: float, group_id: int) -> int:
        cursor = await db.execute(
            "INSERT INTO locations (name, lat, lon, group_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, lat, lon, group_id, now),
        )
        return cursor.lastrowid

    async def add_department(name: str, location_ids: list):
        cursor = await db.execute(
            "INSERT INTO departments (name, created_at) VALUES (?, ?)", (name, now)
        )
        for location_id in location_ids:
            await db.execute(
                "INSERT INTO department_locations (department_id, location_id) VALUES (?, ?)",
                (cursor.lastrowid, location_id),
            )

    async def add_group(chat_id: int):
        # Titles are filled in when the admin opens the catalog
        await db.execute(
            "INSERT OR IGNORE INTO tg_groups (chat_id, title, updated_at) VALUES (?, NULL, ?)",
            (chat_id, now),
        )

    seeded = []
    if LEGACY_GROUP_ID is not None:
        shop_ids = [await add_location(name, lat, lon, LEGACY_GROUP_ID) for name, lat, lon in LEGACY_SHOPS]
        await add_department(LEGACY_SHOP_DEPARTMENT, shop_ids)
        await add_group(LEGACY_GROUP_ID)
        seeded.append(f"{len(shop_ids)} shops")

    factory = (LEGACY_PRODUCTION_LAT, LEGACY_PRODUCTION_LON, LEGACY_PRODUCTION_GROUP_ID)
    if None not in factory:
        factory_id = await add_location(FACTORY_LOCATION_NAME, *factory)
        for name in LEGACY_FACTORY_DEPARTMENTS:
            await add_department(name, [factory_id])
        await add_group(LEGACY_PRODUCTION_GROUP_ID)
        seeded.append(f"{FACTORY_LOCATION_NAME} + {len(LEGACY_FACTORY_DEPARTMENTS)} departments")

    if seeded:
        logger.info("[catalog] seeded from the old setup: %s", ", ".join(seeded))
    else:
        logger.info("[catalog] empty catalog created; the admin fills it via /filiallar")


# "I.T.R xodimlari" is called "Rahbariyat" now
ITR_OLD_NAME = "I.T.R xodimlari"
ITR_NEW_NAME = "Rahbariyat"


async def _rename_itr_department(db):
    """
    One-time rename "I.T.R xodimlari" -> "Rahbariyat" (the employees' department text
    follows it, like an admin rename). Safe to repeat: nothing happens once the old
    name is gone, or when the admin already made a "Rahbariyat" department.
    attendance.branch keeps the old name as history.
    """
    cursor = await db.execute("SELECT id FROM departments WHERE name = ?", (ITR_OLD_NAME,))
    row = await cursor.fetchone()
    if row is None:
        return
    cursor = await db.execute("SELECT 1 FROM departments WHERE name = ?", (ITR_NEW_NAME,))
    if await cursor.fetchone():
        logger.warning("[catalog] both %r and %r exist; nothing renamed", ITR_OLD_NAME, ITR_NEW_NAME)
        return
    await db.execute("UPDATE departments SET name = ? WHERE id = ?", (ITR_NEW_NAME, row[0]))
    await db.execute("UPDATE employees SET department = ? WHERE department_id = ?", (ITR_NEW_NAME, row[0]))
    logger.info("[catalog] department %r renamed to %r", ITR_OLD_NAME, ITR_NEW_NAME)


async def _make_factory_the_base(db):
    """
    "Zavod" stops being a branch: it becomes the base work place, used by every
    department that has no shops. Its links to departments are not needed any more
    (a department without shops goes to the base by itself), so they are removed.
    Safe to repeat: nothing changes once a base exists and has no links.
    """
    cursor = await db.execute("SELECT id FROM locations WHERE kind = 'base'")
    if await cursor.fetchone() is None:
        cursor = await db.execute(
            "UPDATE locations SET kind = 'base' WHERE name = ?", (FACTORY_LOCATION_NAME,)
        )
        if cursor.rowcount:
            logger.info("[catalog] %r is the base work place now", FACTORY_LOCATION_NAME)
    await db.execute(
        "DELETE FROM department_locations WHERE location_id IN (SELECT id FROM locations WHERE kind = 'base')"
    )


async def _link_rows_by_name(db):
    """
    Give rows that have no id yet the id of the department / location with the same name.
    Only NULL ids are filled, so ids set by the code are never overwritten.
    """
    await db.execute("""
        UPDATE employees
        SET department_id = (SELECT d.id FROM departments d WHERE d.name = employees.department)
        WHERE department_id IS NULL AND department IS NOT NULL
    """)
    await db.execute("""
        UPDATE employees
        SET location_id = (SELECT l.id FROM locations l WHERE l.name = employees.last_branch)
        WHERE location_id IS NULL AND last_branch IS NOT NULL
    """)
    # attendance.branch is a shop name, or (factory departments) the department name:
    # a department with exactly one location stands for that location
    await db.execute("""
        UPDATE attendance
        SET location_id = COALESCE(
            (SELECT l.id FROM locations l WHERE l.name = attendance.branch),
            (SELECT MIN(dl.location_id)
               FROM departments d JOIN department_locations dl ON dl.department_id = d.id
              WHERE d.name = attendance.branch
              GROUP BY d.id HAVING COUNT(*) = 1),
            (SELECT l.id FROM locations l
              WHERE l.kind = 'base' AND attendance.branch IN (SELECT name FROM departments))
        )
        WHERE location_id IS NULL
    """)

    cursor = await db.execute(
        "SELECT COUNT(*) FROM employees WHERE department IS NOT NULL AND department_id IS NULL"
    )
    unlinked = (await cursor.fetchone())[0]
    if unlinked:
        logger.warning("[catalog] %d employee(s) have a department name with no match in departments", unlinked)


async def list_departments(active_only: bool = True):
    """(id, name, is_active) of the departments, in creation order."""
    query = "SELECT id, name, is_active FROM departments"
    if active_only:
        query += " WHERE is_active = 1"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(query + " ORDER BY id")
        return await cursor.fetchall()


async def get_department(department_id: int):
    """(id, name, is_active) or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, name, is_active FROM departments WHERE id = ?", (department_id,)
        )
        return await cursor.fetchone()


async def get_department_by_name(name: str):
    """(id, name, is_active) or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, name, is_active FROM departments WHERE name = ?", (name,)
        )
        return await cursor.fetchone()


async def list_locations(active_only: bool = True, branches_only: bool = False):
    """
    (id, name, lat, lon, group_id, is_active) of the locations, in creation order.
    branches_only: the Savdo shops, without the base work place (Zavod).
    """
    query = "SELECT id, name, lat, lon, group_id, is_active FROM locations WHERE 1 = 1"
    if active_only:
        query += " AND is_active = 1"
    if branches_only:
        query += " AND kind = 'branch'"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(query + " ORDER BY id")
        return await cursor.fetchall()


async def get_base_location():
    """The base work place (Zavod): (id, name, lat, lon, group_id, is_active), or None when not entered yet."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, name, lat, lon, group_id, is_active FROM locations WHERE kind = 'base' ORDER BY id LIMIT 1"
        )
        return await cursor.fetchone()


async def get_location(location_id: int):
    """(id, name, lat, lon, group_id, is_active) or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, name, lat, lon, group_id, is_active FROM locations WHERE id = ?",
            (location_id,),
        )
        return await cursor.fetchone()


async def get_department_locations(department_id: int, active_only: bool = True):
    """
    Branches (shops) of one department: (id, name, lat, lon, group_id, is_active), in
    creation order. The base work place is never linked: a department without
    branches works there.
    """
    query = """SELECT l.id, l.name, l.lat, l.lon, l.group_id, l.is_active
               FROM locations l
               JOIN department_locations dl ON dl.location_id = l.id
               WHERE dl.department_id = ? AND l.kind = 'branch'"""
    if active_only:
        query += " AND l.is_active = 1"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(query + " ORDER BY l.id", (department_id,))
        return await cursor.fetchall()


# ===========================
# Catalog management (admin)
# ===========================
async def catalog_name_taken(table: str, name: str, exclude_id: int | None = None) -> bool:
    """
    True when another department / location already has this name, ignoring case.
    Compared in Python: SQLite NOCASE only folds Latin letters, not Cyrillic.
    """
    assert table in ("departments", "locations")
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(f"SELECT id, name FROM {table}")
        rows = await cursor.fetchall()
    wanted = name.casefold()
    return any(row_name.casefold() == wanted and row_id != exclude_id for row_id, row_name in rows)


async def create_location(name: str, lat: float, lon: float, group_id: int | None, department_ids: list,
                          *, base: bool = False) -> int:
    """
    Create an active location linked to the given departments; returns its id.
    base=True: the base work place (Zavod); it is never linked to departments.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO locations (name, lat, lon, group_id, created_at, kind) VALUES (?, ?, ?, ?, ?, ?)",
            (name, lat, lon, group_id, _now_str(), "base" if base else "branch"),
        )
        if base:
            department_ids = []
        location_id = cursor.lastrowid
        await db.executemany(
            "INSERT OR IGNORE INTO department_locations (department_id, location_id) VALUES (?, ?)",
            [(department_id, location_id) for department_id in department_ids],
        )
        await db.commit()
        return location_id


async def rename_location(location_id: int, name: str):
    """
    New name of a location. Employees' last_branch follows it; attendance.branch
    keeps the old name on purpose (history of where people worked).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE locations SET name = ? WHERE id = ?", (name, location_id))
        await db.execute("UPDATE employees SET last_branch = ? WHERE location_id = ?", (name, location_id))
        await db.commit()


async def update_location_coordinates(location_id: int, lat: float, lon: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE locations SET lat = ?, lon = ? WHERE id = ?", (lat, lon, location_id))
        await db.commit()


async def update_location_group(location_id: int, group_id: int | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE locations SET group_id = ? WHERE id = ?", (group_id, location_id))
        await db.commit()


async def set_location_active(location_id: int, active: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE locations SET is_active = ? WHERE id = ?", (1 if active else 0, location_id))
        await db.commit()


async def get_location_departments(location_id: int):
    """(id, name, is_active) of the departments the location is linked to."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT d.id, d.name, d.is_active FROM departments d
               JOIN department_locations dl ON dl.department_id = d.id
               WHERE dl.location_id = ? ORDER BY d.id""",
            (location_id,),
        )
        return await cursor.fetchall()


async def location_usage(location_id: int):
    """(attendance rows, open shifts, employees that chose it) of a location."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT
                   (SELECT COUNT(*) FROM attendance WHERE location_id = ?),
                   (SELECT COUNT(*) FROM attendance WHERE location_id = ? AND check_out_time IS NULL),
                   (SELECT COUNT(*) FROM employees WHERE location_id = ?)""",
            (location_id, location_id, location_id),
        )
        return await cursor.fetchone()


async def delete_location(location_id: int) -> bool:
    """
    Delete a location that was never used (no attendance, no employee chose it).
    Returns False, and deletes nothing, when it is in use.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT EXISTS (SELECT 1 FROM attendance WHERE location_id = ?)
                   OR EXISTS (SELECT 1 FROM employees WHERE location_id = ?)""",
            (location_id, location_id),
        )
        if (await cursor.fetchone())[0]:
            return False
        await db.execute("DELETE FROM department_locations WHERE location_id = ?", (location_id,))
        await db.execute("DELETE FROM locations WHERE id = ?", (location_id,))
        await db.commit()
        return True


async def create_department(name: str, location_ids: list) -> int:
    """Create an active department linked to the given locations; returns its id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO departments (name, created_at) VALUES (?, ?)", (name, _now_str())
        )
        department_id = cursor.lastrowid
        await db.executemany(
            "INSERT OR IGNORE INTO department_locations (department_id, location_id) VALUES (?, ?)",
            [(department_id, location_id) for location_id in location_ids],
        )
        await db.commit()
        return department_id


async def rename_department(department_id: int, name: str):
    """New name of a department; the employees' department text follows it (tabel, shift flow)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE departments SET name = ? WHERE id = ?", (name, department_id))
        await db.execute("UPDATE employees SET department = ? WHERE department_id = ?", (name, department_id))
        await db.commit()


async def set_department_locations(department_id: int, location_ids: list):
    """Replace the set of locations linked to a department."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM department_locations WHERE department_id = ?", (department_id,))
        await db.executemany(
            "INSERT INTO department_locations (department_id, location_id) VALUES (?, ?)",
            [(department_id, location_id) for location_id in location_ids],
        )
        await db.commit()


async def set_location_departments(location_id: int, department_ids: list):
    """Replace the set of departments a location is linked to."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM department_locations WHERE location_id = ?", (location_id,))
        await db.executemany(
            "INSERT INTO department_locations (department_id, location_id) VALUES (?, ?)",
            [(department_id, location_id) for department_id in department_ids],
        )
        await db.commit()


async def set_department_active(department_id: int, active: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE departments SET is_active = ? WHERE id = ?", (1 if active else 0, department_id))
        await db.commit()


async def department_usage(department_id: int):
    """(active employees, all employees) that have this department."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT COALESCE(SUM(is_active = 1), 0), COUNT(*)
               FROM employees WHERE department_id = ?""",
            (department_id,),
        )
        return await cursor.fetchone()


async def delete_department(department_id: int) -> bool:
    """
    Delete a department no employee ever had. Returns False, and deletes nothing,
    when an employee (even a removed one) references it.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT EXISTS (SELECT 1 FROM employees WHERE department_id = ?)", (department_id,)
        )
        if (await cursor.fetchone())[0]:
            return False
        await db.execute("DELETE FROM department_locations WHERE department_id = ?", (department_id,))
        await db.execute("DELETE FROM positions WHERE department_id = ?", (department_id,))
        await db.execute("DELETE FROM departments WHERE id = ?", (department_id,))
        await db.commit()
        return True


# ===========================
# Positions (job titles inside a department)
# ===========================
async def list_positions(department_id: int, active_only: bool = True):
    """(id, name, is_active) of a department's positions, in creation order."""
    query = "SELECT id, name, is_active FROM positions WHERE department_id = ?"
    if active_only:
        query += " AND is_active = 1"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(query + " ORDER BY id", (department_id,))
        return await cursor.fetchall()


async def get_position(position_id: int):
    """(id, department_id, name, is_active) or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, department_id, name, is_active FROM positions WHERE id = ?", (position_id,)
        )
        return await cursor.fetchone()


async def position_name_taken(department_id: int, name: str, exclude_id: int | None = None) -> bool:
    """True when the department already has a position with this name, ignoring case (Cyrillic too)."""
    wanted = name.casefold()
    return any(
        row_name.casefold() == wanted and row_id != exclude_id
        for row_id, row_name, _ in await list_positions(department_id, active_only=False)
    )


async def create_positions(department_id: int, names: list) -> list:
    """Create active positions in a department; returns their ids."""
    ids = []
    async with aiosqlite.connect(DB_PATH) as db:
        for name in names:
            cursor = await db.execute(
                "INSERT INTO positions (department_id, name, created_at) VALUES (?, ?, ?)",
                (department_id, name, _now_str()),
            )
            ids.append(cursor.lastrowid)
        await db.commit()
    return ids


async def rename_position(position_id: int, name: str):
    """New name. Applications keep the name they were sent with (history)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE positions SET name = ? WHERE id = ?", (name, position_id))
        await db.commit()


async def set_position_active(position_id: int, active: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE positions SET is_active = ? WHERE id = ?", (1 if active else 0, position_id))
        await db.commit()


async def position_usage(position_id: int):
    """(active employees, all employees, applications) that have this position."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT
                   (SELECT COUNT(*) FROM employees WHERE position_id = ? AND is_active = 1),
                   (SELECT COUNT(*) FROM employees WHERE position_id = ?),
                   (SELECT COUNT(*) FROM applications WHERE position_id = ?)""",
            (position_id, position_id, position_id),
        )
        return await cursor.fetchone()


async def delete_position(position_id: int) -> bool:
    """
    Delete a position no employee and no application ever had. Returns False, and
    deletes nothing, when it is in use (then it can only be deactivated).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT EXISTS (SELECT 1 FROM employees WHERE position_id = ?)
                   OR EXISTS (SELECT 1 FROM applications WHERE position_id = ?)""",
            (position_id, position_id),
        )
        if (await cursor.fetchone())[0]:
            return False
        await db.execute("DELETE FROM positions WHERE id = ?", (position_id,))
        await db.commit()
        return True


async def employees_without_position():
    """
    Active employees whose department has active positions but who have none
    chosen (they worked before positions existed): (user_id, full_name, department).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT e.user_id, e.full_name, d.name
               FROM employees e JOIN departments d ON d.id = e.department_id
               WHERE e.is_active = 1 AND e.position_id IS NULL
                 AND EXISTS (SELECT 1 FROM positions p WHERE p.department_id = d.id AND p.is_active = 1)
               ORDER BY d.id, e.full_name"""
        )
        return await cursor.fetchall()


async def get_attendance_location_id(attendance_id: int):
    """location_id of an attendance record (None when unknown)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT location_id FROM attendance WHERE id = ?", (attendance_id,))
        row = await cursor.fetchone()
        return row[0] if row else None


# ===========================
# Telegram groups the bot is in
# ===========================
async def upsert_group(chat_id: int, title: str | None, is_member: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO tg_groups (chat_id, title, is_member, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   title = COALESCE(excluded.title, tg_groups.title),
                   is_member = excluded.is_member,
                   updated_at = excluded.updated_at""",
            (chat_id, title, 1 if is_member else 0, _now_str()),
        )
        await db.commit()


async def migrate_group(old_chat_id: int, new_chat_id: int):
    """A group became a supergroup and got a new id: move the group and its locations to it."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO tg_groups (chat_id, title, is_member, updated_at)
               SELECT ?, title, 1, ? FROM tg_groups WHERE chat_id = ?
               ON CONFLICT(chat_id) DO UPDATE SET is_member = 1""",
            (new_chat_id, _now_str(), old_chat_id),
        )
        await db.execute("UPDATE tg_groups SET is_member = 0 WHERE chat_id = ?", (old_chat_id,))
        await db.execute("UPDATE locations SET group_id = ? WHERE group_id = ?", (new_chat_id, old_chat_id))
        await db.commit()


async def list_groups(members_only: bool = True):
    """(chat_id, title, is_member) of the known groups."""
    query = "SELECT chat_id, title, is_member FROM tg_groups"
    if members_only:
        query += " WHERE is_member = 1"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(query + " ORDER BY title IS NULL, title")
        return await cursor.fetchall()


async def get_group(chat_id: int):
    """(chat_id, title, is_member) or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT chat_id, title, is_member FROM tg_groups WHERE chat_id = ?", (chat_id,)
        )
        return await cursor.fetchone()
