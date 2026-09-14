from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.greenapple import parse, find_next_month_url, _first_of_month_from_url

FIXTURE = Path(__file__).parent / "fixtures" / "greenapple_events.html"


@pytest.fixture
def html():
    return FIXTURE.read_text()


def mock_scrape(html_content):
    """Helper: parse fixture HTML directly, bypassing the browser fetch."""
    return parse(html_content)


def test_scrape_returns_list(html):
    events = mock_scrape(html)
    assert isinstance(events, list)


def test_scrape_finds_all_events(html):
    events = mock_scrape(html)
    assert len(events) == 3


def test_event_has_required_fields(html):
    event = mock_scrape(html)[0]
    assert isinstance(event, RawEvent)
    assert event.title
    assert isinstance(event.start_time, datetime)
    assert event.url


def test_event_title(html):
    events = mock_scrape(html)
    assert events[0].title == '9th Ave: Christian John Wikane with Timothy "T.K." Hampton'


def test_event_start_time(html):
    events = mock_scrape(html)
    # 7:00pm San Francisco time, stored as tz-aware UTC
    assert events[0].start_time == datetime(2026, 5, 4, 19, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


def test_event_url_is_absolute(html):
    events = mock_scrape(html)
    for event in events:
        assert event.url.startswith("https://")


def test_event_location(html):
    events = mock_scrape(html)
    assert "1231 9th Ave" in events[0].location


def test_find_next_month_url_present():
    html = '<html><body><a href="/events/2026/10">Next Month</a></body></html>'
    assert find_next_month_url(html) == "https://greenapplebooks.com/events/2026/10"


def test_find_next_month_url_absent():
    html = '<html><body><a href="/events/2026/08">Previous Month</a></body></html>'
    assert find_next_month_url(html) is None


def test_first_of_month_from_url_parses_absolute():
    from datetime import date
    assert _first_of_month_from_url("https://greenapplebooks.com/events/2027/03") == date(2027, 3, 1)


def test_first_of_month_from_url_parses_relative():
    from datetime import date
    assert _first_of_month_from_url("/events/2026/12/") == date(2026, 12, 1)


def test_first_of_month_from_url_rejects_non_month_urls():
    assert _first_of_month_from_url("https://greenapplebooks.com/events") is None
    assert _first_of_month_from_url("https://greenapplebooks.com/event/2026-09-10/some-slug") is None
