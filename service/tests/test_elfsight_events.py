import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import elfsight_events as ef
from scrapers import riptide

FIXTURE = Path(__file__).parent / "fixtures" / "elfsight_events.json"
UTC = ZoneInfo("UTC")


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text())["payload"]


@pytest.fixture
def events(payload):
    return ef.parse_events(payload, fallback_location="The Riptide", fallback_url="https://www.riptidesf.com/")


def test_parses_all(events):
    assert len(events) == 3
    assert all(isinstance(e, RawEvent) for e in events)


def test_tz_aware_start_to_utc(events):
    # 2026-08-31T19:00:00-07:00 → 2026-09-01T02:00:00Z
    ev = next(e for e in events if e.title == "Open Mic by Charlie Kaupp")
    assert ev.start_time.astimezone(UTC) == datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    assert ev.start_time.tzinfo is not None


def test_description_stripped_of_html(events):
    ev = events[0]
    assert ev.description and "<" not in ev.description and "html-blob" not in ev.description


def test_location_from_venue_address(events):
    ev = events[0]
    assert ev.location and "3639 Taraval" in ev.location


def test_url_falls_back_to_site(events):
    # these events have no buttonLink → fall back to the venue site
    assert all(e.url == "https://www.riptidesf.com/" for e in events)


def test_skips_without_name_or_start():
    evs = ef.parse_events([
        {"name": "", "start": {"dateTime": "2026-09-01T19:00:00-07:00"}},
        {"name": "No start", "start": {"dateTime": None, "date": None}},
    ])
    assert evs == []


def test_matches():
    assert riptide.matches("https://www.riptidesf.com/")
    assert not riptide.matches("https://www.commonwealthclub.org/events")


def test_wrapper_delegates(monkeypatch):
    calls = []
    monkeypatch.setattr(ef, "scrape_events", lambda sid, **kw: calls.append((sid, kw)) or [])
    riptide.scrape()
    assert calls[0][0] == riptide.SOURCE_ID
    assert calls[0][1]["fallback_url"] == riptide.SITE_URL
