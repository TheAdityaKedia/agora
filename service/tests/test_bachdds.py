"""Tests for scrapers/bachdds.py — Bach Dancing & Dynamite Society.

Bach DDS is a WordPress site whose concert calendar is served by VBO Tickets.
The scraper resolves a session for the org, fetches the VBO "showevents" list
(one card per event, with a full synopsis), and drops the LIVESTREAM twin of
each in-person concert. Fixtures are trimmed real responses.
"""
from datetime import datetime, timezone
from pathlib import Path

from scrapers.base import RawEvent
from scrapers import bachdds

FIX = Path(__file__).parent / "fixtures"
SHOWEVENTS = (FIX / "bachdds_showevents.html").read_text()
CALENDAR = (FIX / "bachdds_calendar.html").read_text()
LOADPLUGIN = (FIX / "bachdds_loadplugin.html").read_text()


def test_matches():
    assert bachdds.matches("https://bachddsoc.org/")
    assert bachdds.matches("https://bachddsoc.org/calendar/")
    assert not bachdds.matches("https://www.sfjazz.org/")


def test_parse_site_id():
    assert bachdds.parse_site_id(CALENDAR) == "CE358005-B38C-4936-B414-DF0B9C2F10B4"


def test_parse_session():
    org, sess = bachdds.parse_session(LOADPLUGIN)
    assert org == "7957"
    assert sess == "5f8a289d-aa8b-479a-92e8-a8dcde72f3e5"


def test_parse_events_drops_livestream_twins():
    evs = bachdds.parse_events(SHOWEVENTS)
    # Fixture has 3 cards: 2 in-person jazz + 1 LIVESTREAM → livestream dropped.
    assert len(evs) == 2
    assert all(isinstance(e, RawEvent) for e in evs)
    assert all("LIVESTREAM" not in e.title.upper() for e in evs)


def test_event_datetime_is_utc_from_sf_local():
    evs = bachdds.parse_events(SHOWEVENTS)
    pacheco = next(e for e in evs if "PACHECO" in e.title.upper())
    # Sun 9/27/2026 @ 4:30 PM PDT (UTC-7) → 23:30 UTC.
    assert pacheco.start_time == datetime(2026, 9, 27, 23, 30, tzinfo=timezone.utc)
    assert pacheco.start_time.tzinfo == timezone.utc
    kat = next(e for e in evs if "EDMONSON" in e.title.upper())
    # Sun 12/6/2026 @ 4:30 PM PST (UTC-8) → 00:30 UTC on 12/7.
    assert kat.start_time == datetime(2026, 12, 7, 0, 30, tzinfo=timezone.utc)


def test_event_url_points_at_info_page():
    evs = bachdds.parse_events(SHOWEVENTS)
    pacheco = next(e for e in evs if "PACHECO" in e.title.upper())
    assert pacheco.url == "https://bachddsoc.org/calendar/?pg=selectevent&eid=202281&edid=0"


def test_event_fields_populated():
    evs = bachdds.parse_events(SHOWEVENTS)
    pacheco = next(e for e in evs if "PACHECO" in e.title.upper())
    assert "Half Moon Bay" in pacheco.location
    assert pacheco.image_url and pacheco.image_url.endswith(".jpg")
    # HTML entities in the title are decoded.
    assert "&#8220;" not in pacheco.title and "“" in pacheco.title


def test_description_strips_credit_and_link_noise():
    evs = bachdds.parse_events(SHOWEVENTS)
    pacheco = next(e for e in evs if "PACHECO" in e.title.upper())
    d = pacheco.description
    assert d and "Hailing from Havana" in d
    assert not d.startswith("Sponsored by")
    assert "Artist Website" not in d
    assert "Video 1" not in d and "Video 2" not in d


def test_description_none_when_absent():
    html = """<!doctype html><html><body>
    <div id="CurrentEvents">
      <div class="EventListWrapper EID999999 EDID1" data-event-name="Mystery Trio"
           data-event-category="Music" data-event-subcategory="Jazz">
        <div class="EventListPoster"></div>
        <div class="EventListDetails">
          <div class="TextEventDate">Sun, 10/4/2026 @ 4:30 PM</div>
        </div>
      </div>
    </div></body></html>"""
    evs = bachdds.parse_events(html)
    assert len(evs) == 1
    assert evs[0].title == "Mystery Trio"
    assert evs[0].description is None


def test_scrape_chains_calendar_loadplugin_showevents(monkeypatch):
    def fake_get(url):
        if "loadplugin" in url:
            return LOADPLUGIN
        if "showevents" in url:
            return SHOWEVENTS
        return CALENDAR

    monkeypatch.setattr(bachdds, "_fetch", fake_get)
    evs = bachdds.scrape("https://bachddsoc.org/calendar/")
    assert len(evs) == 2
    assert any("PACHECO" in e.title.upper() for e in evs)
