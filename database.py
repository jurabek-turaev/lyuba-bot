import aiosqlite
from datetime import datetime, date

DB_PATH = "data/attendance.db"


async def _column_exists(db, table: str, column: str) -> bool:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in await cursor.fetchall()]
    return column in columns


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

        # --- Migratsiya: eski bazaga yangi ustunlarni qo'shish (buzmasdan) ---
        # Botning oldingi versiyasidan qolgan baza uchun kerak.
        if not await _column_exists(db, "employees", "is_active"):
            await db.execute("ALTER TABLE employees ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if not await _column_exists(db, "attendance", "archived"):
            await db.execute("ALTER TABLE attendance ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")

        await db.commit()


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


async def get_employee_department(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT department FROM employees WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def save_employee_department(user_id: int, department: str):
    """
    Bo'lim (Savdo / Ishlab chiqarish / Haydovchi / I.T.R xodimlari) — bir marta,
    ro'yxatdan o'tishda saqlanadi. UPSERT — ism hali kiritilmagan bo'lsa ham ishlaydi.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO employees (user_id, department) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET department = excluded.department""",
            (user_id, department),
        )
        await db.commit()


async def reset_employee_department(user_id: int):
    """
    Xodimning bo'limini tozalaydi — shundan keyin u /start bosganda
    bo'lim tanlash savoli yana beriladi. Faqat OWNER_ID chaqira oladi.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE employees SET department = NULL WHERE user_id = ?",
            (user_id,),
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


async def create_check_in(user_id: int, full_name: str, branch: str, distance: int, work_date: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO attendance
               (user_id, full_name, branch, work_date, check_in_time, check_in_distance)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, full_name, branch, work_date, now, distance),
        )
        await db.commit()


async def create_check_out(attendance_id: int, distance: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE attendance
               SET check_out_time = ?, check_out_distance = ?
               WHERE id = ?""",
            (now, distance, attendance_id),
        )
        await db.commit()
    return now


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
    """Hali 'Ketdim' bosilmagan barcha yozuvlarni qaytaradi — auto-close tekshiruvi uchun."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT id, user_id, full_name, branch, work_date, check_in_time, checkout_requested_at
               FROM attendance WHERE check_out_time IS NULL"""
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