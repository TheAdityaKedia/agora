from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

import pytest

from scrapers.base import RawEvent
from scrapers import gamh
from scrapers.gamh import parse, matches, _parse_month_day, _parse_time, VENUE

FIXTURE = Path(__file__).parent / "fixtures" / "gamh_events.html"
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return FIXTURE.read_text()


@pytest.fixture
def frozen_2026():
    real = gamh.datetime

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 9, 20, 12, 0, tzinfo=tz)

    with patch.object(gamh, "datetime", Frozen):
        yield


def test_matches():
    assert matches("https://gamh.com/calendar/")
    assert not matches("https://www.thefillmore.com/shows")


def test_parse_returns_events(html, frozen_2026):
    events = parse(html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_event_fields(html, frozen_2026):
    ev = parse(html)[0]
    assert ev.title
    assert ev.url and ev.url.startswith("https://")  # seetickets URL
    assert ev.location == VENUE
    assert ev.image_url


def test_start_time_reflects_showtime(html, frozen_2026):
    ev = parse(html)[0]
    local = ev.start_time.astimezone(PACIFIC)
    # Fixture shows 8:00PM
    assert (local.hour, local.minute) == (20, 0)


def test_parse_month_day():
    assert _parse_month_day("Sun Sep 20") == (9, 20)
    assert _parse_month_day("bogus") is None


def test_parse_time_am_pm():
    assert _parse_time("8:00PM") == (20, 0)
    assert _parse_time("9:30 AM") == (9, 30)
    assert _parse_time("12:00PM") == (12, 0)
    assert _parse_time("bogus") is None
