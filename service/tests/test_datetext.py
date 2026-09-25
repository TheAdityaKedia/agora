from datetime import date, datetime
from zoneinfo import ZoneInfo

from scrapers.datetext import parse_weekday_date

UTC = ZoneInfo("UTC")


def test_weekday_picks_next_year_when_it_matches():
    # Feb 11 is a Thursday in 2027, not 2026
    assert parse_weekday_date("Thursday, February 11 at 6:30 pm", date(2026, 9, 24)).year == 2027


def test_stale_past_listing_stays_in_the_past():
    # Sunday, September 20 2026 already happened; don't move it to 2027
    got = parse_weekday_date("Sunday, September 20 at 3pm", date(2026, 9, 24))
    assert got == datetime(2026, 9, 20, 22, 0, tzinfo=UTC)


def test_december_listing_seen_in_january_is_last_year():
    # Monday, December 28 2026, seen on Jan 5 2027
    assert parse_weekday_date("Monday, December 28 at 7pm", date(2027, 1, 5)).year == 2026


def test_no_weekday_match_takes_closest():
    # "Friday" is wrong for Oct 8 in any nearby year; fall back to 2026
    assert parse_weekday_date("Friday, October 8 at 7pm", date(2026, 9, 24)).year == 2026


def test_unparseable():
    assert parse_weekday_date("TBA", date(2026, 9, 24)) is None
