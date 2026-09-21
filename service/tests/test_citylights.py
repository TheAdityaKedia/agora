from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.citylights import parse, matches, _parse_datetime, STORE_ADDRESS

FIXTURE = Path(__file__).parent / "fixtures" / "citylights_events.html"
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return FIXTURE.read_text()


def test_matches():
    assert matches("https://citylights.com/events/")
    assert not matches("https://greenapplebooks.com/events")


def test_parse_returns_events(html):
    events = parse(html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_fields(html):
    ev = parse(html)[0]
    assert ev.title == "Walter Riley / Civil Rights and Structural Attacks"
    assert ev.url.startswith("https://citylights.com/events/")
    assert ev.description  # non-empty


def test_parse_start_time_is_tz_aware_utc(html):
    ev = parse(html)[0]
    # 7:00pm San Francisco time on Sep 14, 2026 (PDT), stored as UTC
    assert ev.start_time == datetime(2026, 9, 14, 19, 0, tzinfo=PACIFIC)
    assert ev.start_time.tzinfo is not None


def test_parse_location_instore_vs_virtual(html):
    events = parse(html)
    assert events[0].location == STORE_ADDRESS       # Instore Event
    assert events[1].location == "Virtual Event"     # Virtual Event


def test_parse_datetime_strips_cosmetic_tz():
    dt = _parse_datetime("Tuesday, September 15, 2026, 6:00 pm PST")
    # September is daylight time, so 6pm PDT == 01:00 UTC next day
    assert dt == datetime(2026, 9, 15, 18, 0, tzinfo=PACIFIC)


def test_parse_datetime_rejects_garbage():
    assert _parse_datetime("no date here") is None


def test_parse_extracts_image_url(html):
    events = parse(html)
    assert all(e.image_url and e.image_url.startswith("https://citylights.com/") for e in events)
    assert events[0].image_url.endswith(".jpg")
