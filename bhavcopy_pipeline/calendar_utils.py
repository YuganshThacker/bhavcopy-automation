
"""Trading-date helpers.

We deliberately keep NO hardcoded holiday list: the exchanges simply don't
publish a bhavcopy on holidays, so a missing file == no trading. We only filter
out weekends (the obvious case) to avoid pointless requests.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# India Standard Time = UTC+5:30 (no DST).
IST = timezone(timedelta(hours=5, minutes=30))


def today_ist() -> date:
    return datetime.now(IST).date()


def is_weekday(d: date) -> bool:
    return d.weekday() < 5  # Mon=0 .. Fri=4


def most_recent_weekday(d: date) -> date:
    """Return d if it's a weekday, else the Friday before."""
    while not is_weekday(d):
        d -= timedelta(days=1)
    return d


def weekdays_in_range(start: date, end: date):
    """Yield each weekday from start to end inclusive."""
    d = start
    while d <= end:
        if is_weekday(d):
            yield d
        d += timedelta(days=1)


def parse_date(s: str) -> date:
    return date.fromisoformat(s)
