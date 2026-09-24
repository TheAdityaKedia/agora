import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import sfjazz

FIXTURE = Path(__file__).parent / "fixtures" / "sfjazz_ace.json"
UTC = ZoneInfo("UTC")


@pytest.fixture
def events():
    return sfjazz.parse_events(json.loads(FIXTURE.read_text()))


def test_matches():
    assert sfjazz.matches("https://www.sfjazz.org/calendar/")
    assert not sfjazz.matches("https://www.act-sf.org/")


def test_one_event_per_performance(events):
    # Cyrille Aimée plays two nights → two events (multi-night runs split by date)
    assert len(events) == 3
    assert all(isinstance(e, RawEvent) for e in events)
    aimee = [e for e in events if e.title == "Cyrille Aimée"]
    assert len(aimee) == 2
    assert len({e.start_time for e in aimee}) == 2


def test_time_built_from_display_strings_as_pacific(events):
    ev = next(e for e in events if e.title.startswith("Hiromi"))
    # 10/1/2026 9:30 PM Pacific (PDT, -07:00) → 10/2/2026 04:30 UTC
    assert ev.start_time.astimezone(UTC) == datetime(2026, 10, 2, 4, 30, tzinfo=UTC)
    assert ev.start_time.tzinfo is not None


def test_location_url_image_absolute(events):
    ev = events[0]
    assert ev.location.startswith("SFJAZZ Center — ")
    assert ev.url and ev.url.startswith("https://www.sfjazz.org/tickets/")
    assert ev.image_url and ev.image_url.startswith("https://www.sfjazz.org/media/")


def test_parse_start_bad_input():
    assert sfjazz._parse_start(None, "9:00 PM") is None
    assert sfjazz._parse_start("10/1/2026", "nope") is None


def test_skips_items_without_title_or_time():
    evs = sfjazz.parse_events([
        {"name": "", "eventDateString": "10/1/2026", "eventTimeString": "9:00 PM"},
        {"name": "No time", "eventDateString": "10/1/2026", "eventTimeString": None},
    ])
    assert evs == []
