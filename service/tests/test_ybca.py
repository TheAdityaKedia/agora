from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.ybca import parse, matches, _parse_date_range, VENUE, DEFAULT_HOUR

FIXTURE = Path(__file__).parent / "fixtures" / "ybca_events.html"
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return FIXTURE.read_text()


def test_matches():
    assert matches("https://ybca.org/calendar/")
    assert not matches("https://sfjazz.org/calendar/")


def test_parse_returns_events(html):
    events = parse(html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_event_fields(html):
    ev = parse(html)[0]
    assert ev.title
    assert ev.url and ev.url.startswith("https://ybca.org/")
    assert ev.location == VENUE
    assert ev.image_url and ev.image_url.startswith("https://")
    assert ev.description


def test_start_time_defaults_to_6pm_local(html):
    local = parse(html)[0].start_time.astimezone(PACIFIC)
    assert local.hour == DEFAULT_HOUR


def test_parse_date_range_full_month_names():
    assert _parse_date_range("August 7, 2026–January 3, 2027") == (date(2026, 8, 7), date(2027, 1, 3))


def test_parse_date_range_single_day():
    assert _parse_date_range("August 7, 2026") == (date(2026, 8, 7), None)


def test_parse_date_range_rejects_garbage():
    assert _parse_date_range("Coming soon") == (None, None)
