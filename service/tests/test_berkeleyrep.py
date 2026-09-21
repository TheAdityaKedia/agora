from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.berkeleyrep import (
    parse,
    parse_performances,
    matches,
    _parse_day,
    DEFAULT_HOUR,
    DEFAULT_MINUTE,
    VENUE,
)

FIXTURE = Path(__file__).parent / "fixtures" / "berkeleyrep_events.html"
DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "berkeleyrep_detail.html"
UTC = ZoneInfo("UTC")
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return FIXTURE.read_text()


@pytest.fixture
def detail_html():
    return DETAIL_FIXTURE.read_text()


def test_matches():
    assert matches("https://www.berkeleyrep.org/shows")
    assert not matches("https://us.atgtickets.com/whats-on/san-francisco/")


def test_parse_returns_two_events(html):
    events = parse(html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_event_fields(html):
    ev = parse(html)[0]
    assert ev.title
    assert ev.url and ev.url.startswith("https://www.berkeleyrep.org/")
    assert ev.location == VENUE
    assert ev.image_url and ev.image_url.startswith("https://")
    assert ev.description  # has date range and tagline


def test_start_time_at_default_hour(html):
    local = parse(html)[0].start_time.astimezone(PACIFIC)
    assert (local.hour, local.minute) == (DEFAULT_HOUR, DEFAULT_MINUTE)


def test_parse_day():
    assert _parse_day("Fri, Sep 4, 2026") == date(2026, 9, 4)
    assert _parse_day("bogus") is None


# --- Per-performance parsing (detail page JSON-LD Event list) ---

def _show():
    return RawEvent(
        title="The Cook",
        start_time=datetime(2026, 9, 4, 19, 30, tzinfo=PACIFIC),
        location=VENUE,
        url="https://www.berkeleyrep.org/shows/the-cook-b51d",
        description="Fri, Sep 4, 2026 – Sun, Oct 11, 2026 · A cook's forty-year vow.",
        image_url="https://img.berkeleyrep.org/poster.jpg",
    )


def test_parse_performances_one_event_per_showing(detail_html):
    events = parse_performances(detail_html, show=_show())
    assert len(events) == 4
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_performances_uses_absolute_startdate(detail_html):
    """First Event '2026-09-22T19:00:00-07:00' → exact time, no year guessing."""
    first = parse_performances(detail_html, show=_show())[0]
    local = first.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day, local.hour, local.minute) == (2026, 9, 22, 19, 0)
    assert first.start_time.astimezone(UTC) == datetime(2026, 9, 23, 2, 0, tzinfo=UTC)


def test_parse_performances_same_day_two_showings(detail_html):
    """Sep 24 has a 1:00PM matinee and a 7:00PM evening — two distinct events."""
    sep24 = [e for e in parse_performances(detail_html, show=_show())
             if e.start_time.astimezone(PACIFIC).day == 24]
    assert len(sep24) == 2
    hours = sorted(e.start_time.astimezone(PACIFIC).hour for e in sep24)
    assert hours == [13, 19]


def test_parse_performances_crosses_month_within_run(detail_html):
    """A performance dated Oct 11 keeps its own absolute month/day."""
    oct_events = [e for e in parse_performances(detail_html, show=_show())
                  if e.start_time.astimezone(PACIFIC).month == 10]
    assert len(oct_events) == 1
    local = oct_events[0].start_time.astimezone(PACIFIC)
    assert (local.month, local.day, local.hour) == (10, 11, 14)


def test_parse_performances_url_falls_back_to_show(detail_html):
    """Berkeley Rep has no per-performance URL; each showing links to the show."""
    for ev in parse_performances(detail_html, show=_show()):
        assert ev.url == "https://www.berkeleyrep.org/shows/the-cook-b51d"


def test_parse_performances_carries_title_location_image(detail_html):
    ev = parse_performances(detail_html, show=_show())[0]
    assert ev.title == "The Cook"
    assert ev.location == VENUE
    assert ev.image_url == "https://img.berkeleyrep.org/poster.jpg"


def test_parse_performances_ignores_non_event_objects(detail_html):
    """The BreadcrumbList and WebSite ld+json objects must not become events."""
    assert len(parse_performances(detail_html, show=_show())) == 4


def test_parse_performances_empty_when_no_events():
    assert parse_performances("<html><body>no json-ld</body></html>", show=_show()) == []
