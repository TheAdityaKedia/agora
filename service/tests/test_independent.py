from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.independent import (
    parse, parse_event_description, matches, _parse_aria, VENUE,
)

FIXTURE = Path(__file__).parent / "fixtures" / "independent_events.html"
DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "independent_detail.html"
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return FIXTURE.read_text()


@pytest.fixture
def detail_html():
    return DETAIL_FIXTURE.read_text()


def test_matches():
    assert matches("https://www.theindependentsf.com/calendar/")
    assert not matches("https://gamh.com/calendar/")


def test_parse_returns_events(html):
    events = parse(html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_event_fields(html):
    ev = parse(html)[0]
    assert ev.title
    assert ev.location == VENUE
    assert ev.image_url and ev.image_url.startswith("https://")


def test_detail_url_resolved_from_dialog(html):
    """The .fc-event href is a modal hash (#tw-event-dialog-N); the matching
    dialog holds the real /tm-event/ detail link, resolved to an absolute URL."""
    events = parse(html)
    assert events[0].url == "https://www.theindependentsf.com/tm-event/sondre-lerche/"
    # A site-relative dialog link is resolved against the base URL too.
    assert events[1].url == "https://www.theindependentsf.com/tm-event/dana-and-alden/"


def test_parse_event_description_extracts_artist_bio(detail_html):
    desc = parse_event_description(detail_html)
    assert desc and desc.startswith("Sondre Lerche has always been a romantic")
    assert "Do Not Sell" not in desc  # privacy-banner noise excluded


def test_parse_event_description_none_when_no_artist_list():
    assert parse_event_description("<div class='row'><p>no artist list</p></div>") is None


def test_start_time_from_aria(html):
    """Fixture's first event is 'Sondre Lerche|2026-09-21|8:00 PM'."""
    ev = parse(html)[0]
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day, local.hour, local.minute) == (2026, 9, 21, 20, 0)


def test_parse_aria_basic():
    parsed = _parse_aria("Sondre Lerche|2026-09-21|8:00 PM")
    assert parsed is not None
    title, when = parsed
    assert title == "Sondre Lerche"
    local = when.astimezone(PACIFIC)
    assert (local.year, local.month, local.day, local.hour) == (2026, 9, 21, 20)


def test_parse_aria_rejects_garbage():
    assert _parse_aria("Sondre Lerche") is None
    assert _parse_aria("") is None
    assert _parse_aria(None) is None
