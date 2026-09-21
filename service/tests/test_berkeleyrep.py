from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.berkeleyrep import parse, matches, _parse_day, DEFAULT_HOUR, DEFAULT_MINUTE, VENUE

FIXTURE = Path(__file__).parent / "fixtures" / "berkeleyrep_events.html"
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return FIXTURE.read_text()


def test_matches():
    assert matches("https://www.berkeleyrep.org/shows")
    assert not matches("https://us.atgtickets.com/whats-on/san-francisco/")


def test_parse_returns_two_events(html):
    events = parse(html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_event_fields(html):
    ev = parse(html)[0]
    assert ev.title
    assert ev.url and ev.url.startswith("https://www.berkeleyrep.org/")
    assert ev.location == VENUE
    assert ev.image_url and ev.image_url.startswith("https://")
    assert ev.description  # has date range and tagline


def test_start_time_at_default_hour(html):
    local = parse(html)[0].start_time.astimezone(PACIFIC)
    assert (local.hour, local.minute) == (DEFAULT_HOUR, DEFAULT_MINUTE)


def test_parse_day():
    assert _parse_day("Fri, Sep 4, 2026") == date(2026, 9, 4)
    assert _parse_day("bogus") is None
