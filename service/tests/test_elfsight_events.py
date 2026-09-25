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


# --- "settings" mode: events embedded in the widget boot payload ---------------

BOOT_FIXTURE = Path(__file__).parent / "fixtures" / "elfsight_boot.json"
BOOT_WIDGET = "85b32f03-8a60-406b-8fe1-223ad02c821d"


def _boot_events():
    payload = json.loads(BOOT_FIXTURE.read_text())
    settings = ef.widget_settings(payload, BOOT_WIDGET)
    return ef.parse_settings_events(
        settings, fallback_location="Books Inc.", fallback_url="https://www.booksinc.com/pages/events",
    )


def test_settings_mode_local_date_time_to_utc():
    ev = _boot_events()[0]
    assert ev.title == "MARCUS THOMPSON II: GAME CHANGERS"
    # 2026-11-20 18:30 America/Los_Angeles (PST) -> 02:30 UTC next day
    assert ev.start_time == datetime(2026, 11, 21, 2, 30, tzinfo=UTC)


def test_settings_mode_resolves_location_id_to_name_and_address():
    assert _boot_events()[0].location == "Books Inc. Alameda, 1344 Park St, Alameda, CA 94501"


def test_settings_mode_url_from_primary_action_else_fallback():
    evs = _boot_events()
    assert evs[0].url.startswith("https://www.eventbrite.com/e/marcus-thompson-ii-game-changers")
    assert evs[1].url.startswith("https://www.booksinc.com/pages/events#event-")
    assert evs[1].url != evs[2].url


def test_settings_mode_description_image_and_fallback_location():
    evs = _boot_events()
    assert evs[0].description and "<div>" not in evs[0].description
    assert evs[0].image_url.startswith("https://files.elfsightcdn.com/")
    assert evs[2].location == "Books Inc."  # no location id
