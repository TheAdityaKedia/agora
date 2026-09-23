from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import veezi, balboa, fourstar

FIXTURE = Path(__file__).parent / "fixtures" / "veezi_sessions.html"
UTC = ZoneInfo("UTC")


@pytest.fixture
def html():
    return FIXTURE.read_text()


@pytest.fixture
def events(html):
    return veezi.parse_sessions(html, fallback_location="Balboa Theatre")


def test_one_event_per_session(events):
    """3 films, but Resident Evil has 2 sessions → 4 events (one per showtime)."""
    assert len(events) == 4
    assert all(isinstance(e, RawEvent) for e in events)
    re_events = [e for e in events if e.title == "Resident Evil"]
    assert len(re_events) == 2
    assert len({e.start_time for e in re_events}) == 2  # distinct showtimes


def test_absolute_start_time_to_utc(events):
    ev = next(e for e in events if e.title == "Ghost Dog: Way of the Samurai")
    # 2026-09-22T19:00:00-07:00 → 2026-09-23T02:00:00Z
    assert ev.start_time.astimezone(UTC) == datetime(2026, 9, 23, 2, 0, tzinfo=UTC)
    assert ev.start_time.tzinfo is not None


def test_url_is_purchase_link(events):
    ev = next(e for e in events if e.title == "Ghost Dog: Way of the Samurai")
    assert ev.url and ev.url.startswith("https://ticketing.uswest.veezi.com/purchase/")


def test_location_from_json_ld_address(events):
    ev = events[0]
    assert ev.location and "San Francisco" in ev.location


def test_poster_image_mapped_by_title(events):
    ev = next(e for e in events if e.title == "Resident Evil")
    assert ev.image_url and ev.image_url.startswith("https://ticketing.us.veezi.com/Media/Poster")


def test_no_description(events):
    assert all(e.description is None for e in events)


def test_empty_page():
    assert veezi.parse_sessions("<html><body>no sessions</body></html>") == []


def test_matches():
    assert balboa.matches("https://www.balboamovies.com/calendar-of-events")
    assert not balboa.matches("https://www.4-star-movies.com/calendar-of-events")
    assert fourstar.matches("https://www.4-star-movies.com/calendar-of-events")
    assert not fourstar.matches("https://www.balboamovies.com/calendar-of-events")


def test_thin_wrappers_delegate(monkeypatch):
    calls = []
    monkeypatch.setattr(veezi, "scrape_sessions",
                        lambda token, **kw: calls.append((token, kw)) or [])
    balboa.scrape()
    fourstar.scrape()
    assert calls[0][0] == balboa.SITE_TOKEN
    assert calls[1][0] == fourstar.SITE_TOKEN
