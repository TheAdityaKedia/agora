from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import greatstar
from scrapers.greatstar import (
    parse,
    parse_performances,
    matches,
    _parse_date_range,
    _scrape_show_performances,
    DEFAULT_HOUR,
)
from scrapers.browser import RateLimited

FIXTURES = Path(__file__).parent / "fixtures"
LISTING_FIXTURE = FIXTURES / "greatstar_events.html"
DETAIL_FIXTURE = FIXTURES / "greatstar_tickettailor.html"
UTC = ZoneInfo("UTC")
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return LISTING_FIXTURE.read_text()


@pytest.fixture
def detail_html():
    return DETAIL_FIXTURE.read_text()


def _show():
    return RawEvent(
        title="The Umbilical Brothers",
        start_time=datetime(2026, 9, 25, DEFAULT_HOUR, tzinfo=PACIFIC),
        location=greatstar.VENUE,
        url="https://www.tickettailor.com/events/nx5theatricalllc/2312766",
        description="September 25 - 26, 2026 · The Umbilical Brothers are an international comedy phenomenon.",
        image_url="https://www.greatstartheater.org/shows/poster.png",
    )


# --- Listing (run-level) parsing ---

def test_matches():
    assert matches("https://www.greatstartheater.org/whats-playing")
    assert not matches("https://magictheatre.org/")


def test_parse_returns_run_level_events(html):
    events = parse(html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)
    assert events[0].title == "The Umbilical Brothers"
    # Third-party TicketTailor href is hotlinked as-is
    assert events[0].url == "https://www.tickettailor.com/events/nx5theatricalllc/2312766"
    assert events[0].location == greatstar.VENUE


def test_parse_date_range_single_day():
    assert _parse_date_range("September 28, 2026") == date(2026, 9, 28)


def test_parse_date_range_short_range_uses_start():
    assert _parse_date_range("September 25 - 26, 2026") == date(2026, 9, 25)


# --- Per-performance parsing (TicketTailor JSON-LD Event blocks) ---

def test_parse_performances_one_event_per_occurrence(detail_html):
    events = parse_performances(detail_html, show=_show())
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_performances_extracts_date_and_time(detail_html):
    """Occurrence startDate '2026-09-25T19:00:00-07:00' → exact UTC, no year guessing."""
    events = parse_performances(detail_html, show=_show())
    assert events[0].start_time.astimezone(UTC) == datetime(2026, 9, 26, 2, 0, tzinfo=UTC)
    assert events[1].start_time.astimezone(UTC) == datetime(2026, 9, 27, 2, 0, tzinfo=UTC)
    # SF-local both at 7 PM
    assert events[0].start_time.astimezone(PACIFIC).hour == 19


def test_parse_performances_uses_ticket_url(detail_html):
    ev = parse_performances(detail_html, show=_show())[0]
    assert ev.url == "https://www.tickettailor.com/events/nx5theatricalllc/2312766"


def test_parse_performances_carries_show_fields(detail_html):
    show = _show()
    ev = parse_performances(detail_html, show=show)[0]
    assert ev.title == show.title
    assert ev.image_url == show.image_url
    assert ev.description == show.description
    assert ev.location == show.location


def test_parse_performances_empty_when_no_event_jsonld():
    assert parse_performances("<html><body>no json-ld</body></html>", show=_show()) == []


# --- _scrape_show_performances (browser + fallback wiring) ---

def test_scrape_show_performances_no_url_returns_empty():
    show = _show()
    show.url = None
    assert _scrape_show_performances(object(), show) == []


def test_scrape_show_performances_renders_and_parses(monkeypatch, detail_html):
    monkeypatch.setattr(greatstar, "load_page_html", lambda ctx, url, **kw: detail_html)
    events = _scrape_show_performances(object(), _show())
    assert len(events) == 2


def test_scrape_show_performances_rate_limited_returns_empty(monkeypatch):
    def boom(ctx, url, **kw):
        raise RateLimited(url, 403)
    monkeypatch.setattr(greatstar, "load_page_html", boom)
    assert _scrape_show_performances(object(), _show()) == []


def test_scrape_show_performances_empty_html_falls_back_to_empty(monkeypatch):
    """A non-TicketTailor page (no Event JSON-LD) yields [] so the caller keeps the run-level show."""
    monkeypatch.setattr(greatstar, "load_page_html", lambda ctx, url, **kw: "<html></html>")
    assert _scrape_show_performances(object(), _show()) == []
