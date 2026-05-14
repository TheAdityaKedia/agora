from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from scrapers.greenapple import scrape, RawEvent

FIXTURE = Path(__file__).parent / "fixtures" / "greenapple_events.html"


@pytest.fixture
def html():
    return FIXTURE.read_text()


def mock_scrape(html_content):
    """Helper: run scrape() with HTTP call replaced by fixture HTML."""
    mock_response = MagicMock()
    mock_response.text = html_content
    mock_response.raise_for_status = MagicMock()

    with patch("scrapers.greenapple.requests.get", return_value=mock_response):
        return scrape("https://greenapplebooks.com/events")


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
    assert events[0].start_time == datetime(2026, 5, 4, 19, 0)


def test_event_url_is_absolute(html):
    events = mock_scrape(html)
    for event in events:
        assert event.url.startswith("https://")


def test_event_location(html):
    events = mock_scrape(html)
    assert "1231 9th Ave" in events[0].location
