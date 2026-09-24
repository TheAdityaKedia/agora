from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import ludus, themarsh

FIXTURE = Path(__file__).parent / "fixtures" / "ludus_calendar.html"
UTC = ZoneInfo("UTC")
BASE = "https://themarsh.ludus.com/calendar"


@pytest.fixture
def events():
    return ludus.parse_calendar(FIXTURE.read_text(), base_url=BASE, fallback_location="The Marsh")


def test_skips_past_showtimes(events):
    # fixture has 1 past + 2 upcoming showtimes
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_local_time_to_utc(events):
    ev = next(e for e in events if e.title.startswith("Paul Sussman"))
    # 2026-09-26 5:00 PM PDT -> 2026-09-27 00:00 UTC
    assert ev.start_time.astimezone(UTC) == datetime(2026, 9, 27, 0, 0, tzinfo=UTC)
    assert ev.start_time.tzinfo is not None


def test_location_from_category(events):
    locs = {e.location for e in events}
    assert locs == {"San Francisco", "Berkeley"}


def test_url_is_show_page(events):
    assert all(e.url and "show_page.php?show_id=" in e.url for e in events)


def test_extract_handles_no_blob():
    assert ludus.parse_calendar("<html><body>nothing</body></html>", base_url=BASE) == []


def test_parse_start_bad_input():
    assert ludus._parse_start(None, "7:00 PM") is None
    assert ludus._parse_start("2026-10-01", "nonsense") is None


def test_matches():
    assert themarsh.matches("https://themarsh.org/")
    assert themarsh.matches("https://themarsh.ludus.com/calendar")
    assert not themarsh.matches("https://litquake.org")


def test_wrapper_delegates(monkeypatch):
    calls = []
    monkeypatch.setattr(ludus, "scrape_calendar", lambda u, **kw: calls.append(u) or [])
    themarsh.scrape()
    assert calls == [themarsh.CALENDAR_URL]
