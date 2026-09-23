from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import squarespace_events as se
from scrapers import balboa, fourstar

FIXTURE = Path(__file__).parent / "fixtures" / "squarespace_events.html"
UTC = ZoneInfo("UTC")
BASE = "https://www.balboamovies.com/calendar-of-events"


@pytest.fixture
def html():
    return FIXTURE.read_text()


@pytest.fixture
def events(html):
    return se.parse_events(html, base_url=BASE, fallback_location="Balboa Theatre")


def test_one_event_per_showing(events):
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_title_suffix_stripped(events):
    titles = {e.title for e in events}
    assert titles == {"Ghost Dog: Way of the Samurai", "Resident Evil"}
    assert all("~" not in e.title for e in events)


def test_start_time_local_to_utc(events):
    ev = next(e for e in events if e.title == "Ghost Dog: Way of the Samurai")
    # 2026-09-22 19:00 America/Los_Angeles (PDT) → 2026-09-23 02:00 UTC
    assert ev.start_time.astimezone(UTC) == datetime(2026, 9, 23, 2, 0, tzinfo=UTC)
    assert ev.start_time.tzinfo is not None


def test_url_is_venue_permalink(events):
    ev = next(e for e in events if e.title == "Ghost Dog: Way of the Samurai")
    assert ev.url == BASE + "/ghost-dog-way-of-the-samurai-september-22"


def test_rich_description(events):
    ev = next(e for e in events if e.title == "Ghost Dog: Way of the Samurai")
    assert ev.description and len(ev.description) > 200
    assert "Jarmusch" in ev.description


def test_image_from_thumbnail(events):
    ev = next(e for e in events if e.title == "Ghost Dog: Way of the Samurai")
    assert ev.image_url and ev.image_url.startswith("https://images.squarespace-cdn.com/")


def test_location_fallback(events):
    assert all(e.location == "Balboa Theatre" for e in events)


def test_empty_page():
    assert se.parse_events("<html><body>nothing</body></html>", base_url=BASE) == []


def test_matches():
    assert balboa.matches("https://www.balboamovies.com/calendar-of-events")
    assert not balboa.matches("https://www.4-star-movies.com/calendar-of-events")
    assert fourstar.matches("https://www.4-star-movies.com/calendar-of-events")
    assert not fourstar.matches("https://www.balboamovies.com/calendar-of-events")


def test_thin_wrappers_delegate(monkeypatch):
    calls = []
    monkeypatch.setattr(se, "scrape_collection",
                        lambda url, **kw: calls.append((url, kw)) or [])
    balboa.scrape()
    fourstar.scrape()
    assert calls[0][0] == balboa.CALENDAR_URL
    assert calls[1][0] == fourstar.CALENDAR_URL
