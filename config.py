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


def _get_optional_int(name: str):
    """Like _get_int, but an empty / missing value gives None."""
    value = os.getenv(name)
    return None if value is None or value.strip() == "" else _get_int(name)


def _get_optional_float(name: str):
    """Like _get_float, but an empty / missing value gives None."""
    value = os.getenv(name)
    return None if value is None or value.strip() == "" else _get_float(name)


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
DEVELOPER_ID = _get_int("DEVELOPER_ID")

MAX_DISTANCE = _get_int("MAX_DISTANCE")    # ruxsat etilgan maksimal masofa (metr)
MAX_SHIFT_HOURS = _get_int("MAX_SHIFT_HOURS")  # maksimal smena davomiyligi (soat)

# Bo'limlar, filiallar, ularning koordinatalari va guruhlari endi BAZADA saqlanadi
# va admin tomonidan botda boshqariladi ("🗂 Bo'lim va filiallar" / /filiallar).
#
# Quyidagi qiymatlar faqat ESKI bazani bir martalik ko'chirish uchun kerak
# (database._seed_catalog): katalog jadvallari birinchi marta yaratilganda eski
# guruhlar shu yerdan olinadi. Yangi o'rnatishda ularni bo'sh qoldiring.
LEGACY_GROUP_ID = _get_optional_int("GROUP_ID")                        # savdo guruhi
LEGACY_PRODUCTION_LAT = _get_optional_float("PRODUCTION_LAT")          # zavod koordinatasi
LEGACY_PRODUCTION_LON = _get_optional_float("PRODUCTION_LON")
LEGACY_PRODUCTION_GROUP_ID = _get_optional_int("PRODUCTION_GROUP_ID")  # zavod guruhi

# Rezyume PDF'ini yasaydigan RenderCV alohida virtualenv'da ishlaydi (aiogram bilan
# pydantic versiyasi to'qnashadi). Docker'da /opt/rendercv ichida o'rnatilgan.
# Lokal: python -m venv .venv-rendercv && .venv-rendercv/bin/pip install -r requirements-rendercv.txt
RESUME_RENDERER_PYTHON = os.getenv("RESUME_RENDERER_PYTHON") or (
    ".venv-rendercv/bin/python" if os.path.exists(".venv-rendercv/bin/python") else "/opt/rendercv/bin/python"
)
