import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import tribe_events, birdbeckett

FIXTURE = Path(__file__).parent / "fixtures" / "tribe_events.json"
UTC = ZoneInfo("UTC")


@pytest.fixture
def events():
    data = json.loads(FIXTURE.read_text())
    return tribe_events.parse_events(data["events"], fallback_location="fallback")


def test_parses_all_events(events):
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_decodes_html_entities_in_title(events):
    ev = events[0]
    assert "–" in ev.title and "&#8211;" not in ev.title


def test_utc_start_time(events):
    # utc_start_date "2026-09-25 02:00:00" is already UTC
    assert events[0].start_time.astimezone(UTC) == datetime(2026, 9, 25, 2, 0, tzinfo=UTC)
    assert events[0].start_time.tzinfo is not None


def test_location_from_venue_decoded(events):
    ev = events[0]
    assert ev.location.startswith("Bird & Beckett Books & Records")
    assert "&#038;" not in ev.location
    assert "San Francisco" in ev.location


def test_url_and_image_and_description(events):
    ev = events[0]
    assert ev.url.startswith("https://birdbeckett.com/event/")
    assert ev.image_url and ev.image_url.startswith("https://birdbeckett.com/")
    assert ev.description and "<p" not in ev.description


def test_location_falls_back_when_no_venue():
    evs = tribe_events.parse_events(
        [{"title": "X", "utc_start_date": "2026-10-01 03:00:00"}],
        fallback_location="Home",
    )
    assert evs[0].location == "Home"


def test_skips_events_without_title_or_start():
    evs = tribe_events.parse_events([
        {"title": "", "utc_start_date": "2026-10-01 03:00:00"},
        {"title": "No date", "utc_start_date": None},
    ])
    assert evs == []


def test_matches():
    assert birdbeckett.matches("https://birdbeckett.com/events-calendar/")
    assert not birdbeckett.matches("https://medicinefornightmares.com/events")


def test_wrapper_delegates(monkeypatch):
    calls = []
    monkeypatch.setattr(tribe_events, "scrape_events",
                        lambda base, **kw: calls.append(base) or [])
    birdbeckett.scrape()
    assert calls == [birdbeckett.SITE_BASE]
