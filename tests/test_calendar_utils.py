from datetime import date

from bhavcopy_pipeline import calendar_utils as cu


def test_weekend_rolls_back_to_friday() -> None:
    assert cu.most_recent_weekday(date(2026, 9, 26)) == date(2026, 9, 25)  # Sat
    assert cu.most_recent_weekday(date(2026, 9, 27)) == date(2026, 9, 25)  # Sun


def test_weekday_is_unchanged() -> None:
    assert cu.most_recent_weekday(date(2026, 9, 28)) == date(2026, 9, 28)  # Mon


def test_range_skips_weekends_and_is_inclusive() -> None:
    days = list(cu.weekdays_in_range(date(2026, 9, 25), date(2026, 9, 29)))
    assert days == [date(2026, 9, 25), date(2026, 9, 28), date(2026, 9, 29)]


def test_parse_date_is_iso() -> None:
    assert cu.parse_date("2026-06-05") == date(2026, 6, 5)
