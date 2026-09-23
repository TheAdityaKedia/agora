from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import commonwealthclub as cc

FIXTURES = Path(__file__).parent / "fixtures"
UTC = ZoneInfo("UTC")


@pytest.fixture
def detail_html():
    return (FIXTURES / "commonwealthclub_detail.html").read_text()


@pytest.fixture
def listing_html():
    return (FIXTURES / "commonwealthclub_events.html").read_text()


def test_matches():
    assert cc.matches("https://www.commonwealthclub.org/events")
    assert not cc.matches("https://www.cityarts.net/events/")


def test_listing_links_are_absolute_detail_urls(listing_html):
    urls = cc.parse_listing_links(listing_html)
    assert urls, "expected event links"
    assert all(u.startswith("https://www.commonwealthclub.org/events/2") for u in urls)
    assert len(urls) == len(set(urls))  # deduped


def test_parse_detail_returns_event(detail_html):
    ev = cc.parse_detail(detail_html)
    assert isinstance(ev, RawEvent)
    assert ev.title
    assert ev.description and "<" not in ev.description


def test_start_date_treated_as_utc(detail_html):
    ev = cc.parse_detail(detail_html)
    # JSON-LD startDate 2026-09-25T00:30:00 is UTC (a 5:30 PM PDT talk)
    assert ev.start_time.astimezone(UTC) == datetime(2026, 9, 25, 0, 30, tzinfo=UTC)
    assert ev.start_time.tzinfo is not None


def test_detail_has_location_and_image(detail_html):
    ev = cc.parse_detail(detail_html)
    assert ev.location and "Commonwealth" in ev.location
    assert ev.image_url and ev.image_url.startswith("http")
    assert ev.url and ev.url.startswith("https://www.commonwealthclub.org/")


def test_parse_detail_none_without_event_ld():
    assert cc.parse_detail("<html><body>no ld</body></html>") is None
