"""Tests for scrapers/kronos.py — Kronos Quartet (WordPress REST + detail page).

Kronos tours worldwide; Agora is SF Bay Area only, so the scraper must filter
the global tour list down to Bay Area cities. The clean data source is the
WordPress REST API (`/wp-json/wp/v2/events`): each event's title carries the
city ("San Francisco, California") and the excerpt names the venue. The date +
time live on the detail page as JetEngine dynamic fields.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from scrapers.base import RawEvent
from scrapers import kronos

FIX = Path(__file__).parent / "fixtures"
EVENTS = json.loads((FIX / "kronos_events.json").read_text())
DETAIL_STANFORD = (FIX / "kronos_detail_stanford.html").read_text()
DETAIL_NOTIME = (FIX / "kronos_detail_notime.html").read_text()


def test_matches():
    assert kronos.matches("https://kronosquartet.org/upcoming-events/")
    assert kronos.matches("https://kronosquartet.org/events/detail/stanford/")
    assert not kronos.matches("https://sfjazz.org/")


def test_source_and_name():
    assert kronos.SOURCE == "kronosquartet.org"
    assert kronos.NAME == "Kronos Quartet"


def test_is_bay_area_keeps_bay_cities():
    assert kronos._is_bay_area("San Francisco, California")
    assert kronos._is_bay_area("Stanford, California")
    assert kronos._is_bay_area("Berkeley, California")
    assert kronos._is_bay_area("Oakland, California")
    assert kronos._is_bay_area("San Jose, California")


def test_is_bay_area_drops_non_bay():
    # Foreign / out-of-region
    assert not kronos._is_bay_area("London, England")
    assert not kronos._is_bay_area("Rome, Italy")
    # California but NOT Bay Area — the trap: filtering on "California" is wrong.
    assert not kronos._is_bay_area("La Jolla, California")
    assert not kronos._is_bay_area("Santa Barbara, California")
    assert not kronos._is_bay_area("Los Angeles, California")
    assert not kronos._is_bay_area(None)
    assert not kronos._is_bay_area("")


def test_parse_event_list_extracts_all_candidates():
    cands = kronos.parse_event_list(EVENTS)
    # All 5 fixture events, unfiltered.
    assert len(cands) == 5
    sf = next(c for c in cands if c["location"].startswith("San Francisco"))
    assert sf["title"] == "Kronos Quartet — San Francisco"
    assert sf["location"] == "San Francisco, California"
    assert sf["url"] == "https://kronosquartet.org/events/detail/san-francisco-2/"
    assert sf["description"] and "Herbst Theater" in sf["description"]


def test_parse_event_list_bay_area_filter():
    cands = kronos.parse_event_list(EVENTS)
    bay = [c for c in cands if kronos._is_bay_area(c["location"])]
    cities = sorted(c["location"] for c in bay)
    # Keeps SF + Stanford; drops London, Rome, La Jolla.
    assert cities == ["San Francisco, California", "Stanford, California"]


def test_parse_detail_datetime_date_and_time_to_utc():
    # Stanford: October 11, 2026 AT 02:30 PM (PDT, UTC-7) → 21:30 UTC.
    dt = kronos.parse_detail_datetime(DETAIL_STANFORD)
    assert dt == datetime(2026, 10, 11, 21, 30, tzinfo=timezone.utc)
    assert dt.tzinfo == timezone.utc


def test_parse_detail_datetime_ignores_sidebar_dates():
    # The first jet field is the main date; trailing sidebar dates (Apr 1 2026,
    # etc.) must not win.
    dt = kronos.parse_detail_datetime(DETAIL_STANFORD)
    assert dt.year == 2026 and dt.month == 10 and dt.day == 11


def test_parse_detail_datetime_defaults_time_when_missing():
    # No "AT ..." field → date parses, time defaults (still a valid UTC dt).
    dt = kronos.parse_detail_datetime(DETAIL_NOTIME)
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    # January 29, 2027 in local (Bay Area) time, at the default evening hour.
    local = dt.astimezone(kronos.SOURCE_TZ)
    assert (local.year, local.month, local.day) == (2027, 1, 29)
    assert (local.hour, local.minute) == (kronos.DEFAULT_LOCAL_HOUR,
                                          kronos.DEFAULT_LOCAL_MINUTE)


def test_parse_detail_datetime_empty():
    assert kronos.parse_detail_datetime("<html><body>nothing</body></html>") is None


def test_scrape_builds_bay_area_events(monkeypatch):
    def fake_list():
        return EVENTS

    def fake_detail(url):
        return DETAIL_STANFORD

    monkeypatch.setattr(kronos, "_fetch_event_list", fake_list)
    monkeypatch.setattr(kronos, "_fetch_detail_html", fake_detail)

    evs = kronos.scrape("https://kronosquartet.org/upcoming-events/")
    # Only the 2 Bay Area events survive the filter.
    assert len(evs) == 2
    assert all(isinstance(e, RawEvent) for e in evs)
    assert all(e.start_time.tzinfo == timezone.utc for e in evs)
    titles = sorted(e.title for e in evs)
    assert titles == ["Kronos Quartet — San Francisco", "Kronos Quartet — Stanford"]
    for e in evs:
        assert kronos._is_bay_area(e.location)
        assert e.url and e.url.startswith("https://kronosquartet.org/events/detail/")
        assert e.description


def test_scrape_empty_list(monkeypatch):
    monkeypatch.setattr(kronos, "_fetch_event_list", lambda: [])
    monkeypatch.setattr(kronos, "_fetch_detail_html", lambda url: "")
    assert kronos.scrape("https://kronosquartet.org/upcoming-events/") == []
