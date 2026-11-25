import calendar
import os
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import database as db
from services.worktime import hourly_rate, late_hours, shift_hours

TABLES_DIR = "data/tables"
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Frozen header row and name column, lightly highlighted so they stand out while scrolling
HEADER_FILL = PatternFill("solid", start_color="DDEBF7")
NAME_FILL = PatternFill("solid", start_color="F2F2F2")
LATE_FONT = Font(color="C00000")

# Columns added after "Umumiy soat". They stay empty for employees
# whose schedule has not been set yet.
PAY_HEADER = [
    "Ish grafigi",
    "Kunlik norma (soat)",
    "Oylik norma (soat)",
    "Farq: ortiqcha (soat)",
    "Farq: kechikish (soat)",
    "Belgilangan oylik (so'm)",
    "Soatlik stavka (hisoblangan, so'm)",
    "Ishlagan oylik (so'm)",
]


def _pay_columns(settings, worked_days: list, check_ins: list | None = None) -> list:
    """
    Build the schedule / norms / overtime / lateness / salary cells for one employee.

    settings is (shift_start, shift_end, monthly_salary) or None.
    worked_days is the list of worked hours, one value per day the employee worked.
    check_ins is the first check-in (datetime) of every worked day.

    Every worked day is split into two parts:
    - norm part     = min(hours worked, daily norm)
    - overtime part = max(0, hours worked - daily norm)

    So a day below the norm adds exactly the hours worked to the monthly norm
    (8 h on a 10 h norm gives 8), and monthly norm + overtime = total hours.

    - Monthly norm = sum of the norm parts.
    - Overtime ("Farq: ortiqcha") = sum of the overtime parts, never negative.
    - Lateness ("Farq: kechikish") = hours between the planned start and the first
      check-in of each day, summed and written as a NEGATIVE number (10:30 on a
      09:00 schedule gives -1.5). It is a record for the admin only: it changes
      no other column and is not taken off the earned salary.
    - Fixed monthly salary = the salary the admin entered, shown as is.
    - Hourly rate = (monthly salary / 26) / daily norm.
    - Earned salary = hourly rate * monthly norm. Overtime is NOT paid here:
      the admin sets its price and calculates it by hand.
    """
    if settings is None:
        return [None] * len(PAY_HEADER)

    shift_start, shift_end, monthly_salary = settings
    daily_norm = shift_hours(shift_start, shift_end)
    month_norm = round(sum(min(hours, daily_norm) for hours in worked_days), 2)
    overtime = round(sum(max(0.0, hours - daily_norm) for hours in worked_days), 2)
    late = round(sum(late_hours(check_in, shift_start) for check_in in check_ins or []), 2)
    lateness = -late if late else 0.0

    hourly = earned = None
    if monthly_salary:
        rate = hourly_rate(monthly_salary, daily_norm)
        hourly = round(rate, 2)
        earned = round(rate * month_norm)  # computed from the unrounded rate

    return [
        f"{shift_start}–{shift_end}", daily_norm, month_norm, overtime, lateness,
        monthly_salary, hourly, earned,
    ]


async def generate_monthly_tabel(year: int, month: int) -> str:
    """
    Build one tabel for the given month:
    Xodim F.I.Sh. | Username | Lavozim | Do'kon/Guruh | <every date> |
    Umumiy ish kuni | Umumiy soat | <schedule, norms, overtime, fixed salary, hourly rate, earned salary>

    All departments (Savdo, Ishlab chiqarish, Haydovchi, I.T.R) are in one sheet,
    told apart by the "Lavozim" column.

    Returns the path of the created .xlsx file.
    """
    rows = await db.get_month_attendance(year, month)
    pay_settings = await db.get_pay_settings()

    days_in_month = calendar.monthrange(year, month)[1]
    date_columns = [date(year, month, d) for d in range(1, days_in_month + 1)]
    date_labels = [d.strftime("%d.%m.%Y") for d in date_columns]

    # user_id bo'yicha guruhlash
    employees = {}
    for user_id, full_name, username, department, branch, work_date, work_hours, check_in_time in rows:
        if user_id not in employees:
            employees[user_id] = {
                "name": full_name,
                "username": username if username else "Mavjud emas",
                "department": department if department else "Aniqlanmagan",
                "branches": [],
                "hours_by_date": {},
                "first_check_in": {},  # date label -> earliest check-in of that day
            }
        # A Savdo employee may work in several shops during the month: list them all
        if branch and branch not in employees[user_id]["branches"]:
            employees[user_id]["branches"].append(branch)

        d = datetime.strptime(work_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        current = employees[user_id]["hours_by_date"].get(d, 0.0)
        employees[user_id]["hours_by_date"][d] = current + (work_hours or 0.0)
        if check_in_time:
            check_in = datetime.strptime(check_in_time, DATETIME_FORMAT)
            first = employees[user_id]["first_check_in"].get(d)
            if first is None or check_in < first:
                employees[user_id]["first_check_in"][d] = check_in

    wb = Workbook()
    ws = wb.active
    ws.title = f"{month:02d}.{year}"

    header = (
        ["Xodim F.I.Sh.", "Username", "Lavozim", "Do'kon/Guruh"]
        + date_labels
        + ["Umumiy ish kuni", "Umumiy soat"]
        + PAY_HEADER
    )
    ws.append(header)

    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="center")

    for user_id, emp in sorted(employees.items(), key=lambda item: item[1]["name"]):
        row = [emp["name"], emp["username"], emp["department"], ", ".join(emp["branches"])]

        worked_days = []  # rounded hours of every day the employee worked
        check_ins = []    # first check-in of every day the employee worked
        total_hours = 0.0

        for label in date_labels:
            hours = emp["hours_by_date"].get(label)
            if hours:
                rounded = round(hours, 2)
                row.append(rounded)
                worked_days.append(rounded)
                total_hours += rounded
                if label in emp["first_check_in"]:
                    check_ins.append(emp["first_check_in"][label])
            else:
                row.append(None)

        total_hours = round(total_hours, 2)
        row.append(len(worked_days))
        row.append(total_hours)
        row.extend(_pay_columns(pay_settings.get(user_id), worked_days, check_ins))
        ws.append(row)

    for i in range(1, len(header) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 14
    ws.column_dimensions["A"].width = 28  # full names

    # The header row and the name column stay on screen while scrolling
    ws.freeze_panes = "B2"
    for cell in ws[1]:
        cell.fill = HEADER_FILL
    for (cell,) in ws.iter_rows(min_row=2, max_col=1):
        cell.fill = NAME_FILL
        cell.font = Font(bold=True)

    # Lateness is negative: shown in red so it is not mistaken for overtime
    late_column = header.index("Farq: kechikish (soat)") + 1
    for (cell,) in ws.iter_rows(min_row=2, min_col=late_column, max_col=late_column):
        if cell.value:
            cell.font = LATE_FONT

    # Thousands separators for money, two decimals for the calculated hourly rate
    for title, number_format in (
        ("Belgilangan oylik (so'm)", "#,##0"),
        ("Soatlik stavka (hisoblangan, so'm)", "#,##0.00"),
        ("Ishlagan oylik (so'm)", "#,##0"),
    ):
        column = header.index(title) + 1
        for cells in ws.iter_rows(min_row=2, min_col=column, max_col=column):
            cells[0].number_format = number_format

    os.makedirs(TABLES_DIR, exist_ok=True)
    file_path = os.path.join(TABLES_DIR, f"Tabel_{month:02d}-{year}.xlsx")
    wb.save(file_path)

    return file_path