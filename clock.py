from datetime import datetime
from zoneinfo import ZoneInfo
from settings import settings

timezone = ZoneInfo(settings["timezone"])


def now():
    return datetime.now(timezone).replace(tzinfo=None)


def today():
    return now().date().isoformat()


def format_date(iso_date):
    return datetime.fromisoformat(iso_date).strftime(settings["date_format"])
