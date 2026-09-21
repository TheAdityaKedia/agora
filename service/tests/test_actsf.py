from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.actsf import (
    parse,
    matches,
    _parse_date_range,
    _parse_month_day,
    DEFAULT_HOUR,
    VENUE,
)


FIXTURE = Path(__file__).parent / "fixtures" / "actsf_events.html"
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return FIXTURE.read_text()


def test_matches():
    assert matches("https://www.act-sf.org/whats-on")
    assert not matches("https://greenapplebooks.com/events")


def test_parse_returns_three_events(html):
    events = parse(html)
    assert len(events) == 3
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_cross_month_range_uses_start_date(html):
    """First fixture card: 'SEP 22–OCT 18, 2026' → start on Sep 22 @ 7pm."""
    ev = parse(html)[0]
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day) == (2026, 9, 22)
    assert local.hour == DEFAULT_HOUR
    assert "SEP 22" in ev.description
    assert "OCT 18" in ev.description
    assert ev.location == VENUE
    assert ev.url and ev.url.startswith("https://www.act-sf.org/")


def test_parse_same_month_shorthand(html):
    """Second fixture card: 'MAR 10-27, 2027' → start on Mar 10, 2027."""
    ev = parse(html)[1]
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day) == (2027, 3, 10)


def test_parse_single_day(html):
    """Third fixture card: 'OCT 21, 2026' → single day."""
    ev = parse(html)[2]
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day) == (2026, 10, 21)


# --- Date parser unit tests, exercising all real formats ---

def test_date_range_en_dash():
    assert _parse_date_range("SEP 22–OCT 18, 2026") == (date(2026, 9, 22), date(2026, 10, 18))


def test_date_range_em_dash():
    assert _parse_date_range("NOV 12—DEC 6, 2026") == (date(2026, 11, 12), date(2026, 12, 6))


def test_date_range_ascii_hyphen():
    assert _parse_date_range("MAY 13-JUN 13, 2027") == (date(2027, 5, 13), date(2027, 6, 13))


def test_date_range_same_month_shorthand():
    assert _parse_date_range("MAR 10-27, 2027") == (date(2027, 3, 10), date(2027, 3, 27))


def test_date_range_single_day():
    assert _parse_date_range("OCT 21, 2026") == (date(2026, 10, 21), date(2026, 10, 21))


def test_date_range_year_wrap_dec_to_jan():
    """A 'DEC 20-JAN 5, 2028' range means start was 2027."""
    assert _parse_date_range("DEC 20-JAN 5, 2028") == (date(2027, 12, 20), date(2028, 1, 5))


def test_date_range_rejects_garbage():
    assert _parse_date_range("Coming soon") is None
    assert _parse_date_range("") is None
    assert _parse_date_range("2026") is None


def test_parse_month_day_case_insensitive():
    assert _parse_month_day("sep 22") == (9, 22)
    assert _parse_month_day("SEP 22") == (9, 22)
    assert _parse_month_day("Sep 22") == (9, 22)


def test_parse_extracts_image_url(html):
    for ev in parse(html):
        assert ev.image_url and ev.image_url.startswith("https://res.cloudinary.com/a-c-t/")
