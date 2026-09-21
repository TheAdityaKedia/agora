from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.atgtickets import (
    parse,
    matches,
    _parse_date_range,
    _parse_day,
    DEFAULT_HOUR,
)

FIXTURE = Path(__file__).parent / "fixtures" / "atgtickets_events.html"
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return FIXTURE.read_text()


def test_matches():
    assert matches("https://us.atgtickets.com/whats-on/san-francisco/")
    assert not matches("https://greenapplebooks.com/events")


def test_parse_returns_two_events(html):
    events = parse(html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_single_date_card(html):
    ev = parse(html)[0]
    assert ev.title  # e.g. "Laurie Anderson"
    assert ev.url and ev.url.startswith("https://us.atgtickets.com/events/")
    assert ev.location  # venue name like "Curran Theatre"
    # Description carries the displayed date and genre bits
    assert ev.description
    # Start time defaults to 7 PM SF-local
    assert ev.start_time.tzinfo is not None
    local = ev.start_time.astimezone(PACIFIC)
    assert local.hour == DEFAULT_HOUR
    assert local.year == 2026


def test_parse_multiday_card_uses_start_date(html):
    """For a 'Sat Sep 26 - Sun Sep 27, 2026' card, start_time is Sep 26 @ 7pm."""
    ev = parse(html)[1]
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day) == (2026, 9, 26)
    assert local.hour == DEFAULT_HOUR
    # The full range should be preserved in the description
    assert "Sep 26" in ev.description
    assert "Sep 27" in ev.description


def test_parse_date_range_single_day():
    r = _parse_date_range("Fri, Sep 25, 2026")
    assert r == (date(2026, 9, 25), date(2026, 9, 25))


def test_parse_date_range_year_only_at_end():
    """Left side has no year; year is inferred from the right side."""
    r = _parse_date_range("Sat, Sep 26 - Sun, Sep 27, 2026")
    assert r == (date(2026, 9, 26), date(2026, 9, 27))


def test_parse_date_range_year_on_both_sides():
    r = _parse_date_range("Sat, Dec 30, 2026 - Sun, Jan 3, 2027")
    assert r == (date(2026, 12, 30), date(2027, 1, 3))


def test_parse_date_range_cross_year_only_end_year():
    """Left has no year; the parsed left ends up after the right → assume previous year."""
    r = _parse_date_range("Sat, Dec 30 - Sun, Jan 3, 2027")
    assert r == (date(2026, 12, 30), date(2027, 1, 3))


def test_parse_date_range_rejects_garbage():
    assert _parse_date_range("Coming soon") is None
    assert _parse_date_range("") is None


def test_parse_day_uses_fallback_year_when_missing():
    assert _parse_day("Sat, Sep 26", 2028) == date(2028, 9, 26)
