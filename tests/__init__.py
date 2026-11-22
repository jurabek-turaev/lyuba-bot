"""
Test settings. They are set here, before any test imports config.py, so the tests
never depend on (or touch) the real .env, database or Telegram.
load_dotenv() does not override variables that already exist.
"""
import logging
import os

# Some tests break things on purpose (e.g. a failing group); keep their logs out of the report
logging.disable(logging.CRITICAL)

TEST_ENV = {
    "BOT_TOKEN": "123456:TEST",
    "OWNER_ID": "1000",
    "DEVELOPER_ID": "1001",
    "MAX_DISTANCE": "200",
    "MAX_SHIFT_HOURS": "12",
    # old setup, migrated once into the catalog
    "GROUP_ID": "-100111",
    "PRODUCTION_LAT": "38.800000",
    "PRODUCTION_LON": "65.700000",
    "PRODUCTION_GROUP_ID": "-100222",
}
os.environ.update(TEST_ENV)
