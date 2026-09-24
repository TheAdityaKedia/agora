"""Tests for scrapers/sunsettrivia.py — trivia operator venue directory."""
import json
from datetime import datetime, timezone

from scrapers.base import RawEvent
from scrapers import sunsettrivia

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)  # Wednesday


def _rsc_html(venues):
    """Build a page whose RSC payload embeds a venues array, escaped the way
    Next.js emits `self.__next_f.push([1,"...json..."])`."""
    inner = '{"venues":' + json.dumps(venues) + "}"
    esc = inner.replace("\\", "\\\\").replace('"', '\\"')
    return f'<html><body><script>self.__next_f.push([1,"{esc}"])</script></body></html>'


_V_SF = {"name": "Pitt's Pub (Outer Sunset)", "address": "4207 Judah St, San Francisco, CA 94122",
         "city": "San Francisco", "zip": "94122", "state": "CA", "region": "Bay Area",
         "dayOfWeek": "Tuesday", "time": "7:00 PM", "eventFrequency": "Weekly",
         "website": "https://pittspub.example", "aiDescription": "Weekly pub quiz at Pitt's Pub."}
_V_OAK = {"name": "Almanac Beer Co. (Oakland)", "address": "1640 18th St, Oakland, CA 94607",
          "city": "Oakland", "zip": "94607", "state": "CA", "region": "Bay Area",
          "dayOfWeek": "Thursday", "time": "6:30 PM", "eventFrequency": "Weekly",
          "website": "https://almanac.example", "aiDescription": "Trivia at Almanac."}
_V_SD = {"name": "Some Bar (San Diego)", "address": "1 A St, San Diego, CA 92101",
         "city": "San Diego", "zip": "92101", "state": "CA", "region": "San Diego",
         "dayOfWeek": "Monday", "time": "7:00 PM", "eventFrequency": "Weekly",
         "website": "https://sd.example", "aiDescription": "SD trivia."}
_V_MONTHLY = {"name": "Monthly Bar (SF)", "address": "2 B St, San Francisco, CA 94110",
              "city": "San Francisco", "zip": "94110", "state": "CA", "region": "Bay Area",
              "dayOfWeek": "Friday", "time": "8:00 PM", "eventFrequency": "Monthly",
              "website": "https://m.example", "aiDescription": "Monthly."}


def test_matches():
    assert sunsettrivia.matches("https://sunsettrivia.com/locations")
    assert not sunsettrivia.matches("https://www.sfbarguide.com/")


def test_parse_venues_extracts_all_from_rsc():
    vs = sunsettrivia.parse_venues(_rsc_html([_V_SF, _V_SD]))
    assert len(vs) == 2
    assert {v["name"] for v in vs} == {"Pitt's Pub (Outer Sunset)", "Some Bar (San Diego)"}


def test_scrape_filters_to_bay_area_and_expands_weekly(monkeypatch):
    html = _rsc_html([_V_SF, _V_OAK, _V_SD, _V_MONTHLY])
    monkeypatch.setattr(sunsettrivia, "_fetch", lambda url: html)
    evs = sunsettrivia.scrape("https://sunsettrivia.com/locations", now=NOW)
    assert all(isinstance(e, RawEvent) for e in evs)
    names = {e.title for e in evs}
    # SF + Oakland (Bay Area, weekly) expanded; San Diego excluded; Monthly skipped.
    assert any(t.startswith("Trivia Night at Pitt's Pub") for t in names)
    assert any(t.startswith("Trivia Night at Almanac Beer Co.") for t in names)
    assert not any("San Diego" in t for t in names)
    assert not any("Monthly Bar" in t for t in names)


def test_occurrences_are_weekly_and_tagged_fields():
    from scrapers.recurrence import DEFAULT_HORIZON_DAYS
    import scrapers.sunsettrivia as st
    st_events = st._venue_events(_V_SF, now=NOW)
    assert len(st_events) >= 4  # Tuesdays in 28 days
    ev = st_events[0]
    assert ev.title == "Trivia Night at Pitt's Pub (Outer Sunset)"
    assert ev.start_time.tzinfo == timezone.utc
    assert "San Francisco" in ev.location
    assert ev.url == "https://pittspub.example"
    assert ev.description and "quiz" in ev.description.lower()
