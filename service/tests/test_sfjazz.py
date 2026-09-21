from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

import pytest

from scrapers.base import RawEvent
from scrapers import sfjazz
from scrapers.sfjazz import parse, matches, _parse_month_day, _parse_time, VENUE

FIXTURE = Path(__file__).parent / "fixtures" / "sfjazz_events.html"
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return FIXTURE.read_text()


@pytest.fixture
def frozen_2026():
    real = sfjazz.datetime

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 9, 20, 12, 0, tzinfo=tz)

    with patch.object(sfjazz, "datetime", Frozen):
        yield


def test_matches():
    assert matches("https://www.sfjazz.org/calendar/")
    assert not matches("https://ybca.org/calendar/")


def test_parse_returns_events(html, frozen_2026):
    events = parse(html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_event_fields(html, frozen_2026):
    ev = parse(html)[0]
    assert ev.title
    # URL is site-relative → resolved absolute
    assert ev.url and ev.url.startswith("https://www.sfjazz.org/")
    # Location includes SFJAZZ address; may append the specific auditorium
    assert VENUE in ev.location
    assert ev.image_url and ev.image_url.startswith("https://www.sfjazz.org/")


def test_parse_month_day():
    assert _parse_month_day("Sep 20") == (9, 20)
    assert _parse_month_day("bogus") is None


def test_parse_time_from_mixed_text():
    assert _parse_time("3:00 PM | Miner Auditorium") == (15, 0)
    assert _parse_time("no time here") is None
