import logging
from datetime import datetime, date

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


async def reset_employee_department(user_id: int):
    """
    Xodimning bo'limini tozalaydi — shundan keyin u /start bosganda
    bo'lim tanlash savoli yana beriladi. Faqat OWNER_ID chaqira oladi.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE employees SET department = NULL, department_id = NULL WHERE user_id = ?",
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
    The last column is the group of the record's location (None when it has no group).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT a.id, a.user_id, a.full_name, a.branch, a.work_date, a.check_in_time,
                      a.checkout_requested_at, l.group_id
               FROM attendance a
               LEFT JOIN locations l ON l.id = a.location_id
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
    """
    month_prefix = f"{year:04d}-{month:02d}"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT a.user_id, a.full_name, e.username, e.department, a.branch, a.work_date, a.work_hours
               FROM attendance a
               LEFT JOIN employees e ON e.user_id = a.user_id
               WHERE a.work_date LIKE ?
               ORDER BY a.full_name, a.work_date""",
            (f"{month_prefix}%",),
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
# Employee applications (approval flow)
# ===========================
def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


async def create_application(user_id: int, full_name: str, phone: str, username: str | None):
    """
    Store a new pending application and return its id.
    Returns None when the user already has a pending one (double tap, repeated request).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO applications (user_id, full_name, phone, username, status, created_at)
               SELECT ?, ?, ?, ?, 'pending', ?
               WHERE NOT EXISTS (
                   SELECT 1 FROM applications WHERE user_id = ? AND status = 'pending'
               )""",
            (user_id, full_name, phone, username, _now_str(), user_id),
        )
        await db.commit()
        return cursor.lastrowid if cursor.rowcount == 1 else None


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


async def get_pending_applications():
    """Pending applications, oldest first, same columns as get_application()."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT id, user_id, full_name, phone, username, status, created_at
               FROM applications WHERE status = 'pending' ORDER BY id"""
        )
        return await cursor.fetchall()


async def decide_application(app_id: int, status: str) -> bool:
    """
    Give a pending application a final status ('rejected' or 'blocked').
    Returns False when it was not pending any more (already decided).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE applications SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
            (status, _now_str(), app_id),
        )
        await db.commit()
        return cursor.rowcount == 1


async def approve_application(app_id: int):
    """
    Approve a pending application and add (or re-activate) the employee in ONE
    transaction. Returns (user_id, full_name), or None when it was not pending.

    A returning employee keeps the department, schedule and salary. A row that
    never had a name (unfinished registration) loses its leftover department,
    so the person chooses it again after the approval.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE applications SET status = 'approved', decided_at = ? WHERE id = ? AND status = 'pending'",
            (_now_str(), app_id),
        )
        if cursor.rowcount != 1:
            return None

        cursor = await db.execute(
            "SELECT user_id, full_name, username FROM applications WHERE id = ?", (app_id,)
        )
        user_id, full_name, username = await cursor.fetchone()

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
    Lift a block: the blocked application becomes 'rejected' with the cooldown
    already over, so the user may apply again. False when the user was not blocked.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """UPDATE applications SET status = 'rejected', decided_at = '1970-01-01 00:00:00'
               WHERE user_id = ? AND status = 'blocked'""",
            (user_id,),
        )
        await db.commit()
        return cursor.rowcount > 0


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


# ===========================
# Catalog: departments, locations and Telegram groups
# (moved from config.py into the database, managed by the admin)
# ===========================
# Shared location created on the first migration for the factory departments
# (Ishlab chiqarish, Haydovchi, I.T.R xodimlari)
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
        # attendance.branch stays as the historical name; location_id is the stable link
        if not await _column_exists(db, "attendance", "location_id"):
            await db.execute("ALTER TABLE attendance ADD COLUMN location_id INTEGER")

        if first_run:
            await _seed_catalog(db)
        await _link_rows_by_name(db)

        await db.commit()


async def _seed_catalog(db):
    """Fill the empty catalog with exactly what config.py / .env describe today."""
    # Imported here so database.py has no module-level dependency on config
    from config import FIXED_DEPARTMENTS, GROUP_ID, PRODUCTION_LOCATION, SAVDO_BRANCHES, WORK_LOCATIONS

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

    shop_ids = []
    for name in SAVDO_BRANCHES:
        info = WORK_LOCATIONS[name]
        shop_ids.append(await add_location(name, info["lat"], info["lon"], info.get("group_id", GROUP_ID)))
    await add_department("Savdo", shop_ids)

    factory_id = await add_location(
        FACTORY_LOCATION_NAME,
        PRODUCTION_LOCATION["lat"],
        PRODUCTION_LOCATION["lon"],
        PRODUCTION_LOCATION["group_id"],
    )
    for name in FIXED_DEPARTMENTS:
        await add_department(name, [factory_id])

    # Groups already in use; titles are filled in when the bot sees them
    for chat_id in {GROUP_ID, PRODUCTION_LOCATION["group_id"]}:
        await db.execute(
            "INSERT OR IGNORE INTO tg_groups (chat_id, title, updated_at) VALUES (?, NULL, ?)",
            (chat_id, now),
        )

    logger.info("[catalog] seeded from config.py: %d shops + %s, %d departments",
                len(shop_ids), FACTORY_LOCATION_NAME, 1 + len(FIXED_DEPARTMENTS))


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
              GROUP BY d.id HAVING COUNT(*) = 1)
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


async def list_locations(active_only: bool = True):
    """(id, name, lat, lon, group_id, is_active) of the locations, in creation order."""
    query = "SELECT id, name, lat, lon, group_id, is_active FROM locations"
    if active_only:
        query += " WHERE is_active = 1"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(query + " ORDER BY id")
        return await cursor.fetchall()


async def get_location(location_id: int):
    """(id, name, lat, lon, group_id, is_active) or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, name, lat, lon, group_id, is_active FROM locations WHERE id = ?",
            (location_id,),
        )
        return await cursor.fetchone()


async def get_department_locations(department_id: int, active_only: bool = True):
    """Locations of one department: (id, name, lat, lon, group_id, is_active), in creation order."""
    query = """SELECT l.id, l.name, l.lat, l.lon, l.group_id, l.is_active
               FROM locations l
               JOIN department_locations dl ON dl.location_id = l.id
               WHERE dl.department_id = ?"""
    if active_only:
        query += " AND l.is_active = 1"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(query + " ORDER BY l.id", (department_id,))
        return await cursor.fetchall()