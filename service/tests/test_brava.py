"""Tests for the Brava Theater Center scraper.

Brava runs on Squarespace. The /events listing gives one card per show/run
(a single date or a multi-day range). Individual showtimes are NOT broken out
anywhere Squarespace exposes (no per-day subEvent, no per-occurrence ICS), but
each show's detail page carries a schema.org `Event` JSON-LD with the *actual*
start datetime and venue — an accuracy win over the listing's placeholder time.
`parse_performances` reads that JSON-LD; `parse` still yields run-level shows.
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import brava
from scrapers.brava import parse, parse_performances, matches

FIXTURES = Path(__file__).parent / "fixtures"
LISTING_FIXTURE = FIXTURES / "brava_events.html"
DETAIL_FIXTURE = FIXTURES / "brava_detail.html"
UTC = ZoneInfo("UTC")
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def listing_html():
    return LISTING_FIXTURE.read_text()


@pytest.fixture
def detail_html():
    return DETAIL_FIXTURE.read_text()


def _show():
    """A run-level show as produced by parse() — the input to parse_performances."""
    return RawEvent(
        title="2026 HUMP! Film Festival – Fall Season",
        start_time=datetime(2026, 9, 17, 19, 30, tzinfo=PACIFIC),
        location=brava.VENUE,
        url="https://www.brava.org/all-events/humpfallfestival2026-6zp3n-hp5pk-w2pcm",
        description="Rental · September 17, 2026 – September 19, 2026",
        image_url="https://images.squarespace-cdn.com/poster.jpg",
    )


# --- listing (run-level) still parses ---

def test_matches():
    assert matches("https://www.brava.org/events")
    assert not matches("https://magictheatre.org/")


def test_parse_returns_run_level_shows(listing_html):
    events = parse(listing_html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


# --- per-performance parsing from detail-page schema.org Event JSON-LD ---

def test_parse_performances_one_event(detail_html):
    """Squarespace stores each event as a single Event → one performance."""
    events = parse_performances(detail_html, show=_show())
    assert len(events) == 1
    assert isinstance(events[0], RawEvent)


def test_parse_performances_uses_actual_startdate(detail_html):
    """JSON-LD startDate '2026-09-17T18:30:00-0700' → exact time, not the 7:30 guess."""
    ev = parse_performances(detail_html, show=_show())[0]
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day) == (2026, 9, 17)
    assert (local.hour, local.minute) == (18, 30)
    assert ev.start_time.astimezone(UTC) == datetime(2026, 9, 18, 1, 30, tzinfo=UTC)


def test_parse_performances_location_from_jsonld(detail_html):
    ev = parse_performances(detail_html, show=_show())[0]
    assert "Brava Theater Center" in ev.location
    assert "2781 24th Street" in ev.location


def test_parse_performances_passthrough_title_image_url(detail_html):
    show = _show()
    ev = parse_performances(detail_html, show=show)[0]
    assert ev.title == show.title
    assert ev.image_url == show.image_url
    assert ev.url == show.url  # Brava has no per-performance ticket URL
    assert ev.description == show.description


def test_parse_performances_single_date_event():
    """A single-showing event JSON-LD yields one accurately-timed performance."""
    html = (
        '<script type="application/ld+json">'
        '{"name":"Live Music with Khalia","startDate":"2026-10-03T20:00:00-0700",'
        '"endDate":"2026-10-03T22:00:00-0700",'
        '"location":{"name":"Brava Theater Center","@type":"Place"},'
        '"@context":"http://schema.org","@type":"Event"}'
        '</script>'
    )
    events = parse_performances(html, show=_show())
    assert len(events) == 1
    assert events[0].start_time.astimezone(UTC) == datetime(2026, 10, 4, 3, 0, tzinfo=UTC)


def test_parse_performances_empty_when_no_event_jsonld():
    """No Event JSON-LD → [] so the caller falls back to the run-level event."""
    assert parse_performances("<html><body>no json-ld here</body></html>", show=_show()) == []


def test_parse_performances_empty_ignores_non_event_jsonld():
    """WebSite/LocalBusiness JSON-LD without an Event → [] (fallback)."""
    html = (
        '<script type="application/ld+json">'
        '{"name":"Brava","@context":"http://schema.org","@type":"WebSite"}'
        '</script>'
    )
    assert parse_performances(html, show=_show()) == []
