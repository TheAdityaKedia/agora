"""Tests for scrapers/sfbarguide.py — SF Bar Guide recurring bar nights."""
from datetime import datetime, timezone
from pathlib import Path

from scrapers.base import RawEvent
from scrapers import sfbarguide

FIX = Path(__file__).parent / "fixtures"
HOME = (FIX / "sfbarguide_home.html").read_text()
BAR = (FIX / "sfbarguide_bar.html").read_text()
NOW = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)


def test_matches():
    assert sfbarguide.matches("https://www.sfbarguide.com/")
    assert not sfbarguide.matches("https://sunsettrivia.com/")


def test_find_bar_urls_from_itemlist():
    urls = sfbarguide.find_bar_urls(HOME)
    assert urls == [
        "https://www.sfbarguide.com/bar/the-orbit-room",
        "https://www.sfbarguide.com/bar/abbey-tavern",
    ]


def test_parse_bar_events_expands_weekly_occurrences(_now=NOW):
    evs = sfbarguide.parse_bar_events(BAR, now=NOW)
    assert all(isinstance(e, RawEvent) for e in evs)
    # Trivia (anchor 9/24) → 4 occ; Bingo (anchor 9/30) → 3 occ, in 28d = 7
    assert len(evs) == 7
    trivia = [e for e in evs if e.title.startswith("Trivia Night")]
    assert len(trivia) == 4


def test_title_embeds_venue_for_unique_dedup():
    evs = sfbarguide.parse_bar_events(BAR, now=NOW)
    assert any(e.title == "Trivia Night at Abbey Tavern" for e in evs)


def test_event_fields_populated():
    ev = next(e for e in sfbarguide.parse_bar_events(BAR, now=NOW)
              if e.title.startswith("Trivia"))
    assert ev.start_time.tzinfo == timezone.utc
    assert "Abbey Tavern" in ev.location
    assert "4100 Geary" in ev.location
    assert ev.url == "https://www.sfbarguide.com/bar/abbey-tavern"
    assert ev.description and "Trivia" in ev.description


def test_scrape_walks_homepage_then_bar_pages(monkeypatch):
    # Stub the two fetchers: homepage → HOME, any bar url → BAR.
    monkeypatch.setattr(sfbarguide, "_fetch", lambda url: HOME if url.rstrip("/").endswith("sfbarguide.com") else BAR)
    evs = sfbarguide.scrape("https://www.sfbarguide.com/", now=NOW)
    # 2 bars × 7 occ (see parse test) = 14
    assert len(evs) == 14
    assert any(e.title == "Trivia Night at Abbey Tavern" for e in evs)
