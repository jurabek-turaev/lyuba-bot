from datetime import datetime, timedelta

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def calculate_hours(check_in_time: str, check_out_time: str) -> float:
    """
    check_in_time / check_out_time — database.py da saqlanadigan
    "YYYY-MM-DD HH:MM:SS" formatidagi to'liq sana-vaqt satrlari.

    Ikkalasi ham to'liq sanani o'z ichiga olgani uchun, ish 15:00 da
    boshlanib ertasi kuni 01:00 da tugasa ham (tungi smena), farq
    to'g'ri hisoblanadi — chunki kun chegarasidan o'tish avtomatik
    hisobga olinadi.
    """
    t_in = datetime.strptime(check_in_time, DATETIME_FORMAT)
    t_out = datetime.strptime(check_out_time, DATETIME_FORMAT)

    if t_out <= t_in:
        # Nazariy jihatdan bo'lmasligi kerak, lekin ehtiyot uchun
        return 0.0

    delta = t_out - t_in
    hours = delta.total_seconds() / 3600
    return round(hours, 2)


def shift_hours(shift_start: str, shift_end: str) -> float:
    """
    Length of a planned daily shift in hours.

    shift_start / shift_end are "HH:MM" strings. If the end is not later than
    the start, the shift is treated as an overnight shift
    (22:00 -> 06:00 gives 8.0). Equal times give 24.0.
    """
    t_start = datetime.strptime(shift_start, "%H:%M")
    t_end = datetime.strptime(shift_end, "%H:%M")

    delta = t_end - t_start
    if delta.total_seconds() <= 0:
        delta += timedelta(days=1)

    return round(delta.total_seconds() / 3600, 2)