"""Tests for the Dawn Club scraper (thin Squarespace Events Collection wrapper).

Dawn Club publishes its shows as a Squarespace events collection on its /music
page, so the parsing is exercised by scrapers/squarespace_events.py; these tests
confirm the wrapper wires the right collection URL + location, and pin the
extraction/tz/url/description/empty behaviour against a trimmed real fixture.
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import squarespace_events as se
from scrapers import dawnclub

FIXTURE = Path(__file__).parent / "fixtures" / "dawnclub_music.html"
UTC = ZoneInfo("UTC")


@pytest.fixture
def html():
    return FIXTURE.read_text()


@pytest.fixture
def events(html):
    return se.parse_events(html, base_url=dawnclub.CALENDAR_URL,
                           fallback_location=dawnclub.ADDRESS)


def test_matches():
    assert dawnclub.matches("https://www.dawnclub.com/music")
    assert dawnclub.matches("https://dawnclub.com/")
    assert not dawnclub.matches("https://www.balboamovies.com/calendar-of-events")


def test_source_and_name():
    assert dawnclub.SOURCE == "dawnclub.com"
    assert dawnclub.NAME == "The Dawn Club"


def test_extraction(events):
    assert len(events) == 3
    assert all(isinstance(e, RawEvent) for e in events)
    titles = {e.title for e in events}
    assert "Jinx Jones" in titles


def test_start_time_local_to_utc(events):
    ev = next(e for e in events if e.title == "Jinx Jones")
    # 2026-09-23 8:00 PM America/Los_Angeles (PDT) → 2026-09-24 03:00 UTC
    assert ev.start_time.astimezone(UTC) == datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
    assert ev.start_time.tzinfo is not None


def test_missing_time_falls_back_to_midnight(events):
    # The multiday card exposes no start time → midnight SF-local.
    ev = next(e for e in events if e.title == "Late Night: Kevin Person")
    # 2026-09-25 00:00 PDT → 2026-09-25 07:00 UTC
    assert ev.start_time.astimezone(UTC) == datetime(2026, 9, 25, 7, 0, tzinfo=UTC)


def test_url_is_venue_permalink(events):
    ev = next(e for e in events if e.title == "Jinx Jones")
    assert ev.url.startswith("https://www.dawnclub.com/music/jinx-jones")


def test_description_present(events):
    ev = next(e for e in events if e.title == "Jinx Jones")
    assert ev.description and "Jinx" in ev.description


def test_location_fallback(events):
    assert all(e.location == dawnclub.ADDRESS for e in events)


def test_empty_page():
    assert se.parse_events("<html><body>nothing here</body></html>",
                           base_url=dawnclub.CALENDAR_URL) == []


def test_scrape_delegates_to_shared_lib(monkeypatch):
    calls = []
    monkeypatch.setattr(se, "scrape_collection",
                        lambda url, **kw: calls.append((url, kw)) or [])
    dawnclub.scrape()
    assert calls[0][0] == dawnclub.CALENDAR_URL
    assert calls[0][1]["fallback_location"] == dawnclub.ADDRESS
