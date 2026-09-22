from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

import pytest

from scrapers.base import RawEvent
from datetime import date
from scrapers import sfjazz
from scrapers.sfjazz import (
    parse, matches, _parse_month_day, _parse_time, VENUE,
    _month_calendar_url, _next_month, parse_detail_description,
)

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


def test_parse_uses_base_year(html):
    """`base_year=2027` puts the fixture's events in 2027, not "current year"."""
    events = parse(html, base_year=2027)
    assert len(events) == 2
    for ev in events:
        assert ev.start_time.astimezone(PACIFIC).year == 2027


def test_month_calendar_url_format():
    assert _month_calendar_url(date(2026, 10, 1)) == (
        "https://www.sfjazz.org/calendar/?date=2026-10-01&layout=A"
    )


def test_next_month_wraps_at_year_boundary():
    assert _next_month(date(2026, 11, 1)) == date(2026, 12, 1)
    assert _next_month(date(2026, 12, 1)) == date(2027, 1, 1)


def test_parse_detail_description_picks_first_rich_text():
    """The blurb is the first .rich-text on the page. Later .rich-text blocks
    are personnel lists, address+phones, cookie banner — skip them.
    """
    html = """
    <html><body>
      <div class="rich-text">Marcus Miller is a jazz renaissance man. He was
        instrumental to Miles Davis's resurgence in the 1980s and helps us
        celebrate Miles's centennial with music from the We Want Miles album.</div>
      <div class="rich-text">Marcus Miller bass Russell Gunn trumpet</div>
      <div class="rich-text">SFJAZZ CENTER 201 Franklin Street</div>
    </body></html>
    """
    desc = parse_detail_description(html)
    assert desc is not None
    assert desc.startswith("Marcus Miller is a jazz renaissance")


def test_parse_detail_description_skips_expired_show():
    """Past productions show a placeholder — don't use it as the description."""
    html = """
    <html><body>
      <div class="rich-text">All performances for this production have passed.
        Please visit the calendar to see what else is upcoming at SFJAZZ!</div>
      <div class="rich-text">SFJAZZ CENTER 201 Franklin Street</div>
    </body></html>
    """
    assert parse_detail_description(html) is None


def test_parse_detail_description_none_when_no_rich_text():
    assert parse_detail_description("<html><body></body></html>") is None
