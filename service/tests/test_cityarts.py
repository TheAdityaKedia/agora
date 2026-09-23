from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import cityarts

FIXTURES = Path(__file__).parent / "fixtures"
UTC = ZoneInfo("UTC")


@pytest.fixture
def detail_html():
    return (FIXTURES / "cityarts_detail.html").read_text()


@pytest.fixture
def listing_html():
    return (FIXTURES / "cityarts_events.html").read_text()


def test_matches():
    assert cityarts.matches("https://www.cityarts.net/events/")
    assert not cityarts.matches("https://www.commonwealthclub.org/events")


def test_listing_links(listing_html):
    urls = cityarts.parse_listing_links(listing_html)
    assert urls
    assert all(u.startswith("https://www.cityarts.net/event/") for u in urls)
    assert len(urls) == len(set(urls))


def test_parse_detail_title_from_page_title(detail_html):
    ev = cityarts.parse_detail(detail_html, "https://www.cityarts.net/event/kristin-hannah/")
    assert isinstance(ev, RawEvent)
    assert ev.title == "Kristin Hannah"  # " | City Arts & Lectures" stripped
    assert ev.url.endswith("/event/kristin-hannah/")


def test_pacific_time_converted_to_utc(detail_html):
    ev = cityarts.parse_detail(detail_html)
    # Wednesday, September 23, 2026 7:30pm PDT → 2026-09-24 02:30 UTC
    assert ev.start_time.astimezone(UTC) == datetime(2026, 9, 24, 2, 30, tzinfo=UTC)
    assert ev.start_time.tzinfo is not None


def test_location_strips_venue_prefix(detail_html):
    ev = cityarts.parse_detail(detail_html)
    assert ev.location == "Sydney Goldstein Theater"


def test_description_is_subtitle_before_date(detail_html):
    ev = cityarts.parse_detail(detail_html)
    assert ev.description and ev.description.startswith("Kristin Hannah in conversation with")
    # the date line is not part of the description
    assert "2026" not in ev.description


def test_parse_detail_none_without_date():
    assert cityarts.parse_detail("<html><body><title>X</title></body></html>") is None
