from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.independent import parse, matches, _parse_aria, VENUE

FIXTURE = Path(__file__).parent / "fixtures" / "independent_events.html"
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return FIXTURE.read_text()


def test_matches():
    assert matches("https://www.theindependentsf.com/calendar/")
    assert not matches("https://gamh.com/calendar/")


def test_parse_returns_events(html):
    events = parse(html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_event_fields(html):
    ev = parse(html)[0]
    assert ev.title
    assert ev.location == VENUE
    # No public URL from the calendar view
    assert ev.url is None
    assert ev.image_url and ev.image_url.startswith("https://")


def test_start_time_from_aria(html):
    """Fixture's first event is 'Sondre Lerche|2026-09-21|8:00 PM'."""
    ev = parse(html)[0]
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day, local.hour, local.minute) == (2026, 9, 21, 20, 0)


def test_parse_aria_basic():
    parsed = _parse_aria("Sondre Lerche|2026-09-21|8:00 PM")
    assert parsed is not None
    title, when = parsed
    assert title == "Sondre Lerche"
    local = when.astimezone(PACIFIC)
    assert (local.year, local.month, local.day, local.hour) == (2026, 9, 21, 20)


def test_parse_aria_rejects_garbage():
    assert _parse_aria("Sondre Lerche") is None
    assert _parse_aria("") is None
    assert _parse_aria(None) is None
