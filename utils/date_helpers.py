"""Timezone and seasonality date helpers."""

from datetime import datetime, timedelta, timezone

# Mountain Time is UTC-7 (standard) or UTC-6 (daylight)
# For scheduling purposes we use a fixed offset; EventBridge handles DST.
MOUNTAIN_OFFSET = timezone(timedelta(hours=-7))


def get_mountain_time() -> datetime:
    """Return current time in Mountain Time."""
    return datetime.now(MOUNTAIN_OFFSET)


def get_today_mountain() -> str:
    """Return today's date string in Mountain Time (YYYY-MM-DD)."""
    return get_mountain_time().strftime("%Y-%m-%d")


def get_current_month() -> int:
    """Return current month number in Mountain Time."""
    return get_mountain_time().month


def get_lookback_date(lookback_days: int) -> str:
    """Return the date N days ago as YYYY-MM-DD."""
    dt = get_mountain_time() - timedelta(days=lookback_days)
    return dt.strftime("%Y-%m-%d")


def get_week_boundaries() -> tuple:
    """Return (week_starting, week_ending) for the current week.

    Week runs Monday to Sunday.
    """
    now = get_mountain_time()
    # Monday of current week
    monday = now - timedelta(days=now.weekday())
    # Sunday of current week
    sunday = monday + timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


def days_since(date_str: str) -> int:
    """Return the number of days between a date string and today."""
    then = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=MOUNTAIN_OFFSET)
    now = get_mountain_time()
    return (now - then).days


def format_date(date_str: str) -> str:
    """Format YYYY-MM-DD to a human-readable date like 'February 10, 2026'."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%B %d, %Y").replace(" 0", " ")
