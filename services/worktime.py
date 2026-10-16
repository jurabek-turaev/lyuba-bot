from datetime import datetime

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