"""Uzbek formatting shared by the candidate and the admin side of the hiring flow."""
from datetime import date, datetime

MONTHS_GENITIVE = [
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentyabr", "oktyabr", "noyabr", "dekabr",
]
WEEKDAYS = ["dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba"]


def money(amount: int | None) -> str:
    """5000000 -> '5 000 000 so'm'."""
    if amount is None:
        return "—"
    return f"{amount:,}".replace(",", " ") + " so'm"


def age(birth_date: str | None, today: date | None = None) -> int | None:
    """Age in full years for "DD.MM.YYYY", None when the date is missing or invalid."""
    try:
        born = datetime.strptime(birth_date or "", "%d.%m.%Y").date()
    except ValueError:
        return None
    today = today or date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def month_year(value: str | None) -> str:
    """'2021-03' -> '03.2021', 'present' -> 'hozirgacha'."""
    if not value:
        return "?"
    if value == "present":
        return "hozirgacha"
    year, month = value.split("-")
    return f"{month}.{year}"


def experience_months(jobs: list | None, today: date | None = None) -> int:
    """Total months of all jobs with a known start (an open job counts until today)."""
    today = today or date.today()
    total = 0
    for job in jobs or []:
        if not job.get("start"):
            continue
        start_year, start_month = map(int, job["start"].split("-"))
        end = job.get("end") or "present"
        if end == "present":
            end_year, end_month = today.year, today.month
        else:
            end_year, end_month = map(int, end.split("-"))
        total += max(0, (end_year - start_year) * 12 + end_month - start_month + 1)
    return total


def duration(months: int) -> str:
    """26 -> '2 yil 2 oy'."""
    years, months = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{years} yil")
    if months or not years:
        parts.append(f"{months} oy")
    return " ".join(parts)


def interview_time(value: str) -> str:
    """'2026-11-25 10:00' -> '25-noyabr, seshanba, 10:00'."""
    moment = datetime.strptime(value, "%Y-%m-%d %H:%M")
    return (f"{moment.day}-{MONTHS_GENITIVE[moment.month - 1]}, "
            f"{WEEKDAYS[moment.weekday()]}, {moment:%H:%M}")
