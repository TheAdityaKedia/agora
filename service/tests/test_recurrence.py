"""Tests for scrapers/recurrence.py — expand a recurring schedule into dated
occurrences within a rolling window."""
from datetime import datetime, timezone

from scrapers.recurrence import expand_occurrences

PT = "-07:00"  # Pacific daylight offset used in the fixtures


def _now(y, m, d, h=9):
    return datetime(y, m, d, h, 0, tzinfo=timezone.utc)


def test_weekly_expands_to_four_occurrences_in_28_days():
    # First occurrence Thu 2026-09-24 19:30 PT; weekly; 28-day horizon from
    # a "now" of 2026-09-23.
    occ = expand_occurrences("2026-09-24T19:30:00-07:00", "P1W",
                             horizon_days=28, now=_now(2026, 9, 23))
    assert len(occ) == 4  # 9/24, 10/1, 10/8, 10/15
    assert all(o.tzinfo == timezone.utc for o in occ)
    # each is 7 days apart
    assert [(occ[i+1] - occ[i]).days for i in range(3)] == [7, 7, 7]
    # first is the anchor converted to UTC (19:30 PT = 02:30 UTC next day)
    assert occ[0] == datetime(2026, 9, 25, 2, 30, tzinfo=timezone.utc)


def test_biweekly_steps_two_weeks():
    occ = expand_occurrences("2026-09-24T19:30:00-07:00", "P2W",
                             horizon_days=28, now=_now(2026, 9, 23))
    assert len(occ) == 2  # 9/24, 10/8
    assert (occ[1] - occ[0]).days == 14


def test_unknown_frequency_emits_only_the_single_anchor():
    # Monthly / unparseable → don't fabricate a cadence; emit just the given date.
    occ = expand_occurrences("2026-09-24T19:30:00-07:00", "P1M",
                             horizon_days=28, now=_now(2026, 9, 23))
    assert len(occ) == 1
    occ2 = expand_occurrences("2026-09-24T19:30:00-07:00", None,
                              horizon_days=28, now=_now(2026, 9, 23))
    assert len(occ2) == 1


def test_anchor_beyond_horizon_yields_nothing():
    occ = expand_occurrences("2026-12-01T19:30:00-07:00", "P1W",
                             horizon_days=28, now=_now(2026, 9, 23))
    assert occ == []


def test_includes_earlier_today_occurrence():
    # An occurrence earlier today (past `now` but same local day) is still
    # within the window (matches the exporter's start-of-today floor).
    occ = expand_occurrences("2026-09-23T10:00:00-07:00", "P1W",
                             horizon_days=28, now=_now(2026, 9, 23, 20))  # now 8pm UTC
    assert len(occ) >= 1
    assert occ[0].date() == datetime(2026, 9, 23, 17, 0, tzinfo=timezone.utc).date()


def test_rolls_stale_anchor_forward_into_window():
    # If the anchor is in the past, roll forward by the cadence to the first
    # occurrence within the window (defensive — the source usually gives the
    # next date already).
    occ = expand_occurrences("2026-09-01T19:30:00-07:00", "P1W",
                             horizon_days=28, now=_now(2026, 9, 23))
    assert occ, "should roll forward to occurrences on/after today"
    assert all(o >= _now(2026, 9, 23).replace(hour=0) for o in occ) or occ[0].date() >= datetime(2026, 9, 23).date()


def test_next_weekly_start_finds_upcoming_weekday():
    from scrapers.recurrence import next_weekly_start
    # now = Wed 2026-09-23 09:00 UTC (Tue 2026-09-23 in... actually Wed PT).
    dt = next_weekly_start("Thursday", "6:30 PM", now=_now(2026, 9, 23, 12))
    local = dt.astimezone(__import__("zoneinfo").ZoneInfo("America/Los_Angeles"))
    assert (local.month, local.day, local.hour, local.minute) == (9, 24, 18, 30)
    assert dt.tzinfo == timezone.utc


def test_next_weekly_start_includes_today_if_matching_weekday():
    from scrapers.recurrence import next_weekly_start
    # 2026-09-23 is a Wednesday (in PT). Asking for Wednesday → today.
    dt = next_weekly_start("Wednesday", "7:00 PM", now=_now(2026, 9, 23, 12))
    local = dt.astimezone(__import__("zoneinfo").ZoneInfo("America/Los_Angeles"))
    assert (local.month, local.day) == (9, 23)


def test_next_weekly_start_bad_input_returns_none():
    from scrapers.recurrence import next_weekly_start
    assert next_weekly_start("Someday", "7:00 PM", now=_now(2026, 9, 23)) is None
    assert next_weekly_start("Monday", "not a time", now=_now(2026, 9, 23)) is None
