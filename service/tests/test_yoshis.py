"""Tests for scrapers/yoshis.py — Yoshi's (Oakland jazz club) scraper.

Fixtures are trimmed real captures:
  - yoshis_events.html         : the /events listing (ul.eventListings, 14 li)
  - yoshis_detail_single.html  : a single-performance show (.bigdate/.bigtime)
  - yoshis_detail_multi.html   : a multi-performance run (ul.event-full-list)
"""
from datetime import datetime, timezone
from pathlib import Path

from scrapers.base import RawEvent
from scrapers import yoshis

FIX = Path(__file__).parent / "fixtures"
LISTING = (FIX / "yoshis_events.html").read_text()
SINGLE = (FIX / "yoshis_detail_single.html").read_text()
MULTI = (FIX / "yoshis_detail_multi.html").read_text()

SINGLE_URL = "https://yoshis.com/events/buy-tickets/isaiah-collier/detail"
MULTI_URL = "https://yoshis.com/events/buy-tickets/spyro-gyra-6/detail"


def test_matches():
    assert yoshis.matches("https://yoshis.com/events")
    assert yoshis.matches("https://www.yoshis.com/events/buy-tickets/x/detail")
    assert not yoshis.matches("https://sfjazz.org/calendar/")


def test_parse_listing_returns_unique_detail_urls():
    urls = yoshis.parse_listing(LISTING)
    # 14 li in the fixture collapse to 9 unique shows (Spyro/Curtis/Miles repeat)
    assert len(urls) == 9
    assert SINGLE_URL in urls
    assert MULTI_URL in urls
    # each URL is absolute and a yoshis.com detail page
    assert all(u.startswith("https://yoshis.com/events/buy-tickets/") for u in urls)
    assert all(u.endswith("/detail") for u in urls)
    # de-duped: spyro-gyra appears once despite 3 listing rows
    assert sum(1 for u in urls if "spyro-gyra" in u) == 1


def test_parse_detail_single_returns_one_event():
    evs = yoshis.parse_detail(SINGLE, SINGLE_URL)
    assert len(evs) == 1
    ev = evs[0]
    assert isinstance(ev, RawEvent)
    assert ev.title == "ISAIAH COLLIER: ‘COLLIER PLAYS COLTRANE’"
    assert ev.url == SINGLE_URL
    assert "Oakland" in ev.location
    assert ev.image_url == (
        "https://yoshis.com/userfiles/events/images/2849/isaiah-collier2-copy.jpeg"
    )


def test_single_start_time_is_show_time_in_utc():
    ev = yoshis.parse_detail(SINGLE, SINGLE_URL)[0]
    # "Sun September 27, 2026", "Show: 7:00 PM" PDT (UTC-7) -> 2026-09-28 02:00 UTC
    assert ev.start_time == datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)
    assert ev.start_time.tzinfo == timezone.utc


def test_single_description_extracted_and_meaningful():
    ev = yoshis.parse_detail(SINGLE, SINGLE_URL)[0]
    assert ev.description
    assert "Chicago native" in ev.description
    # noise like the mailing-list prompt must not leak in
    assert "Mailing List" not in ev.description


def test_parse_detail_multi_one_event_per_performance():
    evs = yoshis.parse_detail(MULTI, MULTI_URL)
    # 3 performances: 9/29 7:30, 9/30 7:30, 9/30 9:30
    assert len(evs) == 3
    assert all(e.title == "SPYRO GYRA" for e in evs)
    assert all(e.url == MULTI_URL for e in evs)
    starts = sorted(e.start_time for e in evs)
    # PDT (UTC-7): 9/29 19:30 -> 9/30 02:30Z ; 9/30 19:30 -> 10/1 02:30Z ; 9/30 21:30 -> 10/1 04:30Z
    assert starts == [
        datetime(2026, 9, 30, 2, 30, tzinfo=timezone.utc),
        datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc),
        datetime(2026, 10, 1, 4, 30, tzinfo=timezone.utc),
    ]
    # distinct performances, no fabricated duplicates
    assert len({e.start_time for e in evs}) == 3


def test_parse_detail_empty_html_returns_nothing():
    assert yoshis.parse_detail("<html><body></body></html>", SINGLE_URL) == []


def test_scrape_walks_listing_then_details(monkeypatch):
    def fake_fetch(url):
        if url.rstrip("/").endswith("/events"):
            return LISTING
        if "spyro-gyra" in url:
            return MULTI
        return SINGLE  # every other detail URL -> the single-show fixture

    monkeypatch.setattr(yoshis, "_fetch", fake_fetch)
    evs = yoshis.scrape("https://yoshis.com/events")
    # 8 single-show URLs * 1 perf + 1 multi URL * 3 perf = 11 events
    assert len(evs) == 11
    assert any(e.title == "SPYRO GYRA" for e in evs)
    assert all(e.start_time.tzinfo == timezone.utc for e in evs)
