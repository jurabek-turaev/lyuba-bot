import calendar
import os
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

import database as db

TABLES_DIR = "data/tables"


async def generate_monthly_tabel(year: int, month: int) -> str:
    """
    Berilgan oy uchun bitta tabel yaratadi:
    Xodim F.I.Sh. | Username | Lavozim | Do'kon/Guruh | <har bir sana> | Umumiy ish kuni | Umumiy soat

    Barcha bo'limlar (Savdo, Ishlab chiqarish, Haydovchi, I.T.R) bitta jadvalda,
    "Lavozim" ustuni orqali farqlanadi.

    Qaytaradi: yaratilgan .xlsx faylining yo'li.
    """
    rows = await db.get_month_attendance(year, month)

    days_in_month = calendar.monthrange(year, month)[1]
    date_columns = [date(year, month, d) for d in range(1, days_in_month + 1)]
    date_labels = [d.strftime("%d.%m.%Y") for d in date_columns]

    # user_id bo'yicha guruhlash
    employees = {}
    for user_id, full_name, username, department, branch, work_date, work_hours in rows:
        if user_id not in employees:
            employees[user_id] = {
                "name": full_name,
                "username": username if username else "Mavjud emas",
                "department": department if department else "Aniqlanmagan",
                "branch": branch,
                "hours_by_date": {},
            }

        d = datetime.strptime(work_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        current = employees[user_id]["hours_by_date"].get(d, 0.0)
        employees[user_id]["hours_by_date"][d] = current + (work_hours or 0.0)

    wb = Workbook()
    ws = wb.active
    ws.title = f"{month:02d}.{year}"

    header = (
        ["Xodim F.I.Sh.", "Username", "Lavozim", "Do'kon/Guruh"]
        + date_labels
        + ["Umumiy ish kuni", "Umumiy soat"]
    )
    ws.append(header)

    for cell in ws[1]:
        cell.font = Font(bold=True)

    for emp in sorted(employees.values(), key=lambda e: e["name"]):
        row = [emp["name"], emp["username"], emp["department"], emp["branch"]]

        total_days = 0
        total_hours = 0.0

        for label in date_labels:
            hours = emp["hours_by_date"].get(label)
            if hours:
                rounded = round(hours, 2)
                row.append(rounded)
                total_days += 1
                total_hours += rounded
            else:
                row.append(None)

        row.append(total_days)
        row.append(round(total_hours, 2))
        ws.append(row)

    for i in range(1, len(header) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 14

    os.makedirs(TABLES_DIR, exist_ok=True)
    file_path = os.path.join(TABLES_DIR, f"Tabel_{month:02d}-{year}.xlsx")
    wb.save(file_path)

    return file_path