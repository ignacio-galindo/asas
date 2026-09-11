"""Values are reduced to calendar dates before comparison, and "today" is the
host's calendar, not the server's."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from asas_validation import (
    Rule,
    as_date,
    clock,
    configure,
    declare_rules,
    evaluate,
    raise_if_invalid,
    today,
)

FIXED = date(2026, 9, 11)


def _not_future():
    declare_rules((Rule("m", "not_future", ("at",), "future", "m.at_future"),))


def test_as_date_accepts_date_datetime_and_iso_strings():
    assert as_date(date(2026, 1, 2)) == date(2026, 1, 2)
    assert as_date(datetime(2026, 1, 2, 23, 59)) == date(2026, 1, 2)
    assert as_date("2026-01-02") == date(2026, 1, 2)
    assert as_date("2026-01-02T23:59:00") == date(2026, 1, 2)
    assert as_date(None) is None
    assert as_date("") is None


def test_as_date_rejects_non_dates_loudly():
    """A crash here is a wiring bug (wrong field, wrong type); it must not become a
    silent skip that lets the invalid value through, nor an opaque TypeError."""
    with pytest.raises(ValueError, match="'dob'.*not an ISO date"):
        as_date("yesterday", "dob")
    with pytest.raises(ValueError, match="'dob'.*int"):
        as_date(20260101, "dob")


def test_datetime_value_no_longer_crashes_against_today():
    """Pre-0.12 a datetime in ``changes`` raised TypeError (datetime vs date) → 500."""
    _not_future()
    assert evaluate("m", None, {"at": datetime(2026, 9, 11, 12, 0)}, today=FIXED) == []
    assert evaluate("m", None, {"at": datetime(2026, 9, 12, 0, 0)}, today=FIXED)[0].code == "m.at_future"


def test_aware_datetime_is_read_in_the_configured_zone():
    """21:00 UTC on the 11th is already the 12th in Dubai — a future date there."""
    _not_future()
    at = datetime(2026, 9, 11, 21, 0, tzinfo=timezone.utc)
    configure(timezone="Asia/Dubai")
    assert evaluate("m", None, {"at": at}, today=FIXED)[0].code == "m.at_future"
    configure(timezone="UTC")
    assert evaluate("m", None, {"at": at}, today=FIXED) == []


def test_configured_timezone_drives_today():
    configure(timezone="Asia/Dubai")
    assert today() == datetime.now(ZoneInfo("Asia/Dubai")).date()
    configure(timezone=ZoneInfo("Pacific/Kiritimati"))  # tzinfo objects work too
    assert today() == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()


def test_configured_clock_wins_over_timezone_and_explicit_today_wins_over_both():
    _not_future()
    configure(today=lambda: date(2000, 1, 1), timezone="Asia/Dubai")
    assert today() == date(2000, 1, 1)
    assert evaluate("m", None, {"at": date(2000, 1, 2)})[0].code == "m.at_future"
    assert evaluate("m", None, {"at": date(2000, 1, 2)}, today=date(2000, 1, 3)) == []


def test_configure_with_no_arguments_resets_to_system_date():
    configure(today=lambda: date(2000, 1, 1))
    configure()
    assert clock.timezone() is None
    assert today() == date.today()


def test_unknown_timezone_fails_at_configure_time():
    with pytest.raises(Exception):  # zoneinfo.ZoneInfoNotFoundError (a KeyError subclass)
        configure(timezone="Mars/Olympus_Mons")


def test_raise_if_invalid_accepts_today_override():
    from fastapi import HTTPException

    _not_future()
    raise_if_invalid("m", None, {"at": "2026-09-11"}, today=FIXED)  # ok
    with pytest.raises(HTTPException):
        raise_if_invalid("m", None, {"at": "2026-09-11"}, today=FIXED - timedelta(days=1))
