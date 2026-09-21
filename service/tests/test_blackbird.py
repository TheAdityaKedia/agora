from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

import pytest

from scrapers.base import RawEvent
from scrapers import blackbird
from scrapers.blackbird import matches, parse, STORE_ADDRESS

FIXTURE = Path(__file__).parent / "fixtures" / "blackbird_events.html"
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return FIXTURE.read_text()


@pytest.fixture
def frozen_now_2026():
    """The fixture has Sep+Oct events; year is inferred from 'today'.

    Freeze the module's `datetime.now(SOURCE_TZ).year` to 2026 so tests are
    stable regardless of when they're run.
    """
    real_datetime = blackbird.datetime

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 9, 22, 12, 0, tzinfo=tz)

    with patch.object(blackbird, "datetime", FrozenDatetime):
        yield


def test_matches():
    assert matches("https://blackbirdsf.com/pages/events")
    assert not matches("https://greenapplebooks.com/events")


def test_parse_returns_two_events(html, frozen_now_2026):
    events = parse(html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_title_and_time(html, frozen_now_2026):
    events = parse(html)
    e0 = events[0]
    assert e0.title  # non-empty
    # First event is in September 2026 (per fixture)
    assert e0.start_time.year == 2026
    assert e0.start_time.month == 9


def test_parse_rolls_year_forward_on_month_wrap(html, frozen_now_2026):
    """Fixture is one Sep + one Oct event (both 2026)."""
    events = parse(html)
    assert events[0].start_time.month == 9
    assert events[1].start_time.month == 10
    assert events[0].start_time.year == 2026
    assert events[1].start_time.year == 2026


def test_parse_no_per_event_url(html, frozen_now_2026):
    for e in parse(html):
        assert e.url is None


def test_parse_default_location_is_store_address(html, frozen_now_2026):
    for e in parse(html):
        assert e.location == STORE_ADDRESS


def test_parse_start_time_is_tz_aware(html, frozen_now_2026):
    for e in parse(html):
        assert e.start_time.tzinfo is not None


def test_parse_extracts_image_url(html, frozen_now_2026):
    """Black Bird's poster is inline `background-image: url(...)`. Real HTML
    also has a spurious trailing `)` inside the URL that must be stripped.
    """
    for e in parse(html):
        assert e.image_url and e.image_url.startswith("https://mahina.b-cdn.net/media/")
        assert not e.image_url.endswith(")")
