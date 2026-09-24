"""Tests for scrapers/biscuitsblues.py — a thin wrapper over the shared
Squarespace Events Collection library (scrapers/squarespace_events.py).

Biscuits & Blues publishes its schedule as a Squarespace Events Collection at
/find-a-show (Eventbrite handles per-show checkout only, so the venue's own
show pages — not eventbrite.com deep links — are the right landing URLs). The
collection cards carry no description, so the wrapper enriches from each show's
detail page (.eventitem-column-content).
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import squarespace_events as se
from scrapers import biscuitsblues

FIXTURES = Path(__file__).parent / "fixtures"
UTC = ZoneInfo("UTC")


@pytest.fixture
def events():
    html = (FIXTURES / "biscuitsblues_findashow.html").read_text()
    return se.parse_events(html, base_url=biscuitsblues.CALENDAR_URL,
                           fallback_location=biscuitsblues.ADDRESS)


def test_matches_website_and_show_path():
    assert biscuitsblues.matches("https://www.biscuitsandblues.com/")
    assert biscuitsblues.matches("https://www.biscuitsandblues.com/find-a-show")
    assert not biscuitsblues.matches("https://www.balboamovies.com/calendar-of-events")


def test_source_and_name():
    assert biscuitsblues.SOURCE == "biscuitsandblues.com"
    assert biscuitsblues.NAME == "Biscuits & Blues"


def test_one_event_per_showing(events):
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)
    assert {e.title for e in events} == {"Oscar LaDell", "Otto Junior"}


def test_start_time_local_to_utc(events):
    ev = next(e for e in events if e.title == "Oscar LaDell")
    # 2026-09-24 6:30 PM America/Los_Angeles (PDT) -> 2026-09-25 01:30 UTC
    assert ev.start_time.astimezone(UTC) == datetime(2026, 9, 25, 1, 30, tzinfo=UTC)
    assert ev.start_time.tzinfo is not None


def test_url_is_venue_show_page_not_eventbrite(events):
    ev = next(e for e in events if e.title == "Oscar LaDell")
    assert ev.url == "https://www.biscuitsandblues.com/find-a-show/20260924oscarladell"
    assert "eventbrite" not in ev.url


def test_image_and_location(events):
    ev = next(e for e in events if e.title == "Oscar LaDell")
    assert ev.image_url and ev.image_url.startswith("https://images.squarespace-cdn.com/")
    assert all(e.location == biscuitsblues.ADDRESS for e in events)


def test_description_enriched_from_detail_page():
    html = (FIXTURES / "biscuitsblues_detail.html").read_text()
    desc = se.parse_detail_description(html)
    assert desc and "soul-blues singer" in desc
    # meta chrome and the trailing CTA are stripped
    assert "Google Calendar" not in desc and "ICS" not in desc
    assert "Buy Tickets" not in desc


def test_empty_page():
    assert se.parse_events("<html><body>no shows</body></html>",
                           base_url=biscuitsblues.CALENDAR_URL) == []


def test_scrape_delegates_with_enrichment(monkeypatch):
    calls = []

    def fake_scrape_collection(url, **kw):
        calls.append((url, kw))
        return []

    monkeypatch.setattr(se, "scrape_collection", fake_scrape_collection)
    biscuitsblues.scrape()
    assert len(calls) == 1
    url, kw = calls[0]
    assert url == biscuitsblues.CALENDAR_URL
    assert kw.get("fallback_location") == biscuitsblues.ADDRESS
    assert kw.get("enrich_descriptions") is True
