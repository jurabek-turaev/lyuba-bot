# config.py
import os

from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise RuntimeError(f"Muhit o'zgaruvchisi topilmadi yoki bo'sh: {name} (.env faylni tekshiring)")
    try:
        return int(value)
    except ValueError:
        raise RuntimeError(f"{name} butun son bo'lishi kerak, hozir: {value!r}")


def _get_float(name: str) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise RuntimeError(f"Muhit o'zgaruvchisi topilmadi yoki bo'sh: {name} (.env faylni tekshiring)")
    try:
        return float(value)
    except ValueError:
        raise RuntimeError(f"{name} son bo'lishi kerak, hozir: {value!r}")


BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi. .env faylga BOT_TOKEN=... qo'shing.")

OWNER_ID = _get_int("OWNER_ID")            # bot egasining Telegram ID'si
GROUP_ID = _get_int("GROUP_ID")            # savdo uchun umumiy guruh ID'si
DEVELOPER_ID = _get_int("DEVELOPER_ID")

MAX_DISTANCE = _get_int("MAX_DISTANCE")    # ruxsat etilgan maksimal masofa (metr)
MAX_SHIFT_HOURS = _get_int("MAX_SHIFT_HOURS")  # maksimal smena davomiyligi (soat)

# Ishlab chiqarish, Haydovchi, I.T.R — hammasi bitta zavod hududida va bitta guruhga yuboriladi
PRODUCTION_LOCATION = {
    "lat": _get_float("PRODUCTION_LAT"),
    "lon": _get_float("PRODUCTION_LON"),
    "group_id": _get_int("PRODUCTION_GROUP_ID"),  # ishlab chiqarish guruhi ID'si
}

WORK_LOCATIONS = {
    "Lyuba Orzu bozor": {"lat": 38.828995, "lon": 65.793966},
    "Eski bozor": {"lat": 38.869932, "lon": 65.801341},
    "Koson Lyuba": {"lat": 39.035179, "lon": 65.583693},
    "Ishlab chiqarish": PRODUCTION_LOCATION,
    "Haydovchi": PRODUCTION_LOCATION,
    "I.T.R xodimlari": PRODUCTION_LOCATION,
}

# Faqat "Savdo" tanlaganlarga do'kon tanlash tugmalari ko'rsatiladi
SAVDO_BRANCHES = ["Lyuba Orzu bozor", "Eski bozor", "Koson Lyuba"]

# Bular do'kon tanlashsiz, to'g'ridan-to'g'ri ishlaydi
FIXED_DEPARTMENTS = ["Ishlab chiqarish", "Haydovchi", "I.T.R xodimlari"]
