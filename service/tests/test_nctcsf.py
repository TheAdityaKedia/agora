from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.nctcsf import (
    parse,
    parse_performances,
    matches,
    VENUE,
    DEFAULT_HOUR,
)

FIXTURE = Path(__file__).parent / "fixtures" / "nctcsf_shows.html"
DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "nctcsf_detail.html"
UTC = ZoneInfo("UTC")
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return FIXTURE.read_text()


@pytest.fixture
def detail_html():
    return DETAIL_FIXTURE.read_text()


def test_matches():
    assert matches("https://nctcsf.org/shows/")
    assert not matches("https://greenapplebooks.com/events")


# --- Listing parse (run-level, used as fallback) ---

def test_parse_returns_two_events(html):
    events = parse(html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_run_level_fields(html):
    ev = parse(html)[0]
    assert ev.title == "Little Shop of Horrors"
    assert ev.url == "https://nctcsf.org/event/little-shop/"
    assert ev.image_url and ev.image_url.startswith("https://nctcsf.org/")
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day) == (2026, 9, 12)
    assert local.hour == DEFAULT_HOUR


def test_parse_cross_year_run(html):
    """'Dec 5, 2026 - Jan 10, 2027' → run starts Dec 5, 2026."""
    ev = parse(html)[1]
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day) == (2026, 12, 5)


# --- Per-performance parsing (detail page schema.org Event JSON-LD) ---

def _show():
    return RawEvent(
        title="Little Shop of Horrors",
        start_time=datetime(2026, 9, 12, 19, 30, tzinfo=PACIFIC),
        location=VENUE,
        url="https://nctcsf.org/event/little-shop/",
        description="Runs Sep 12 - Oct 25, 2026",
        image_url="https://nctcsf.org/wp-content/uploads/2026/01/1200-x-630-2.jpg",
    )


def test_parse_performances_one_event_per_showing(detail_html):
    events = parse_performances(detail_html, show=_show())
    assert len(events) == 3
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_performances_extracts_date_and_time(detail_html):
    """First Event: startDate '2026-09-12T20:00:00-07:00' → Sep 12 2026, 20:00 SF-local."""
    ev = parse_performances(detail_html, show=_show())[0]
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day, local.hour, local.minute) == (2026, 9, 12, 20, 0)
    assert ev.start_time.astimezone(UTC) == datetime(2026, 9, 13, 3, 0, tzinfo=UTC)


def test_parse_performances_matinee_time(detail_html):
    """Second Event is a 2:00PM matinee."""
    ev = parse_performances(detail_html, show=_show())[1]
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.month, local.day, local.hour, local.minute) == (9, 13, 14, 0)


def test_parse_performances_carries_title_image_description(detail_html):
    ev = parse_performances(detail_html, show=_show())[0]
    assert ev.title == "Little Shop of Horrors"
    assert ev.image_url == "https://nctcsf.org/wp-content/uploads/2026/01/1200-x-630-2.jpg"
    assert ev.description == "Runs Sep 12 - Oct 25, 2026"


def test_parse_performances_location_from_jsonld(detail_html):
    ev = parse_performances(detail_html, show=_show())[0]
    assert "Decker Theatre" in ev.location


def test_parse_performances_uses_show_url_when_no_offer(detail_html):
    """NCTC JSON-LD carries no per-performance URL, so each showing links to the show."""
    ev = parse_performances(detail_html, show=_show())[0]
    assert ev.url == "https://nctcsf.org/event/little-shop/"


def test_parse_performances_ignores_non_event_jsonld(detail_html):
    """The WebSite JSON-LD block must not become an event."""
    events = parse_performances(detail_html, show=_show())
    assert all(e.title == "Little Shop of Horrors" for e in events)


def test_parse_performances_absolute_cross_year_date():
    """A Jan 2027 startDate is used verbatim — absolute year, no inference."""
    html = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Event","name":"A Kidman Carol",'
        '"startDate":"2027-01-02T20:00:00-08:00",'
        '"location":{"@type":"Place","name":"Decker Theatre"}}'
        '</script>'
    )
    events = parse_performances(html, show=_show())
    assert len(events) == 1
    local = events[0].start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day, local.hour) == (2027, 1, 2, 20)


def test_parse_performances_empty_when_no_event_jsonld():
    assert parse_performances("<html><body>no json-ld</body></html>", show=_show()) == []
