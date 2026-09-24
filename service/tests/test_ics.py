from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import ics, litquake

FIXTURE = Path(__file__).parent / "fixtures" / "litquake.ics"
UTC = ZoneInfo("UTC")


@pytest.fixture
def events():
    return ics.parse_ics(FIXTURE.read_text(), fallback_location="SF Bay Area")


def test_parses_vevents(events):
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_dtstart_utc(events):
    ev = events[0]
    # DTSTART:20260911T020000Z
    assert ev.start_time.astimezone(UTC) == datetime(2026, 9, 11, 2, 0, tzinfo=UTC)
    assert ev.start_time.tzinfo is not None


def test_summary_and_location_unescaped(events):
    ev = events[0]
    assert ev.title == "Changing Gender: Susan Stryker (SOLD OUT)"
    # LOCATION had escaped commas ("Oakstop California Ballroom\, 1536 Franklin…")
    assert ev.location and "," in ev.location and "\\" not in ev.location


def test_description_stripped(events):
    ev = events[0]
    assert ev.description and "<" not in ev.description and "&nbsp" not in ev.description


def test_url_present(events):
    ev = events[0]
    assert ev.url and "sched.com/event/" in ev.url


def test_ics_escaping_and_tzid():
    text = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\n"
        "SUMMARY:Reading\\, Part 1\n"
        "DTSTART;TZID=America/Los_Angeles:20261001T190000\n"
        "END:VEVENT\nEND:VCALENDAR\n"
    )
    ev = ics.parse_ics(text)[0]
    assert ev.title == "Reading, Part 1"
    # 7pm PDT → 02:00 UTC next day
    assert ev.start_time.astimezone(UTC) == datetime(2026, 10, 2, 2, 0, tzinfo=UTC)


def test_folded_lines_unfolded():
    # RFC 5545 folds by inserting CRLF + a space; unfolding removes both, so the
    # fold marker's space is not content. Here "wrap ped" is folded mid-word.
    text = (
        "BEGIN:VEVENT\n"
        "SUMMARY:A title that is wrap\n ped across lines\n"
        "DTSTART:20261001T190000Z\n"
        "END:VEVENT\n"
    )
    ev = ics.parse_ics(text)[0]
    assert ev.title == "A title that is wrapped across lines"


def test_skips_without_summary_or_start():
    text = "BEGIN:VEVENT\nDTSTART:20261001T190000Z\nEND:VEVENT\n"
    assert ics.parse_ics(text) == []


def test_matches():
    assert litquake.matches("https://litquake2026.sched.com/")
    assert litquake.matches("https://www.litquake.org/")
    assert not litquake.matches("https://oaklandartmurmur.org")


def test_wrapper_delegates(monkeypatch):
    calls = []
    monkeypatch.setattr(ics, "scrape_ics", lambda u, **kw: calls.append(u) or [])
    litquake.scrape()
    assert calls == [litquake.ICS_URL]
