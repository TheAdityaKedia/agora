from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapers import facebook, missionfusion, zyte

FIXTURES = Path(__file__).parent / "fixtures"
EVENT_ID = "1482073670446143"


@pytest.fixture
def listing_html():
    return (FIXTURES / "facebook_listing.html").read_text()


@pytest.fixture
def event_html():
    return (FIXTURES / "facebook_event.html").read_text()


def test_missionfusion_matches():
    assert missionfusion.matches("https://www.facebook.com/MissionFusion/events")
    assert not missionfusion.matches("https://www.facebook.com/SomeOtherPage/events")
    assert not missionfusion.matches("https://www.missionfusion.com/")


def test_parse_listing_skips_cancelled_and_dedupes(listing_html):
    nodes = facebook.parse_listing(listing_html)
    assert [n["id"] for n in nodes] == [EVENT_ID, "876134272104509"]
    assert [facebook.listing_city(n) for n in nodes] == ["San Francisco", "Mendocino"]


def test_parse_event(event_html):
    ev = facebook.parse_event(event_html, EVENT_ID)
    assert ev.title == "Mission Fusion w/ Jonathan, Natalie and Mark"
    # 8pm PDT, not the related event's timestamp elsewhere on the page
    assert ev.start_time == datetime(2026, 10, 4, 3, 0, tzinfo=timezone.utc)
    assert ev.location == "Saint Gregory of Nyssa Episcopal Church, 500 De Haro St, San Francisco, CA 94107"
    assert ev.url == f"https://www.facebook.com/events/{EVENT_ID}/"
    assert "All Levels Class with Mark Carpenter" in ev.description
    assert ev.image_url is None  # fbcdn URLs expire; see parse_event


def test_parse_event_wrong_id_is_none(event_html):
    assert facebook.parse_event(event_html, "123") is None


def test_scrape_page_fetches_only_bay_area_events(monkeypatch, listing_html, event_html):
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return listing_html if url.endswith("upcoming_hosted_events") else event_html

    monkeypatch.setattr(facebook, "fetch_html", fake_fetch)
    events = facebook.scrape_page("MissionFusion")
    assert [e.title for e in events] == ["Mission Fusion w/ Jonathan, Natalie and Mark"]
    # the Mendocino campout's page is never fetched (saves a Zyte credit)
    assert fetched == [facebook.listing_url("MissionFusion"), facebook.event_url(EVENT_ID)]


def test_fetch_uses_zyte_cheap_tier_when_keyed(monkeypatch):
    calls = []
    monkeypatch.setattr(zyte, "is_configured", lambda: True)
    monkeypatch.setattr(zyte, "fetch_html", lambda url, **kw: calls.append((url, kw)) or "<html/>")
    assert facebook.fetch_html("https://www.facebook.com/x") == "<html/>"
    assert calls[0][1]["render"] is False
