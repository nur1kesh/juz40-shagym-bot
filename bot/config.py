import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Almaty"))
DB_PATH = os.getenv("DB_PATH", "data/bot.db")
WORK_START = int(os.getenv("WORK_START_HOUR", "11"))
WORK_END = int(os.getenv("WORK_END_HOUR", "21"))
REMINDER_EVERY_MIN = int(os.getenv("REMINDER_EVERY_MINUTES", "60"))
SLA_HOURS = int(os.getenv("SLA_HOURS", "24"))


def now() -> datetime:
    return datetime.now(TZ)


def in_work_window(dt: datetime | None = None) -> bool:
    """Дүйсенбі-сенбі, 11:00-21:00. Жексенбі - демалыс."""
    dt = dt or now()
    return dt.weekday() != 6 and WORK_START <= dt.hour < WORK_END
