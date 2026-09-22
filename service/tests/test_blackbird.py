import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.blackbird import matches, parse_events, STORE_ADDRESS, EVENTS_URL

FIXTURE = Path(__file__).parent / "fixtures" / "blackbird_api.json"
UTC = ZoneInfo("UTC")


@pytest.fixture
def data():
    return json.loads(FIXTURE.read_text())


def test_matches():
    assert matches("https://blackbirdsf.com/pages/events")
    assert not matches("https://greenapplebooks.com/events")


def test_parse_events_returns_events(data):
    events = parse_events(data)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_events_uses_absolute_startdate(data):
    """startDate is ISO UTC — no year inference needed."""
    ev = parse_events(data)[0]
    assert ev.start_time.astimezone(UTC) == datetime(2026, 9, 23, 2, 0, tzinfo=UTC)


def test_parse_events_strips_html_description(data):
    ev = parse_events(data)[0]
    assert ev.description
    assert "<p>" not in ev.description and "</h3>" not in ev.description
    assert ev.description.startswith("Wild Surf Writers is a women")


def test_parse_events_builds_event_id_url(data):
    ev = parse_events(data)[0]
    assert ev.url == f"{EVENTS_URL}#?event-id=87937"


def test_parse_events_extracts_image_url(data):
    ev = parse_events(data)[0]
    assert ev.image_url == "https://mahina.b-cdn.net/media/September%20_1789599013632.png"


def test_parse_events_location_falls_back_to_store_address(data):
    """The API's location.name is empty for in-store events → store address."""
    for ev in parse_events(data):
        assert ev.location == STORE_ADDRESS


def test_parse_events_start_time_tz_aware(data):
    for ev in parse_events(data):
        assert ev.start_time.tzinfo is not None


def test_parse_events_skips_entries_without_title_or_date():
    bad = {"events": [
        {"id": 1, "title": "No date", "startDate": None},
        {"id": 2, "title": "", "startDate": "2026-10-01T02:00:00.000Z"},
    ]}
    assert parse_events(bad) == []


def test_parse_events_empty_page():
    assert parse_events({"events": []}) == []
