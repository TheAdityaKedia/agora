from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.sfplayhouse import (
    parse,
    parse_performances,
    matches,
    _parse_range,
    VENUE,
)

PACIFIC = ZoneInfo("America/Los_Angeles")
FIXTURES = Path(__file__).parent / "fixtures"


HOME_HTML = """
<html><body>
<a href="https://sfplayhouse.org/2026-2027-season/peter-pan-goes-wrong/">
  <img src="https://sfplayhouse.org/wp-content/uploads/peter-pan.png"/>
</a>
<a href="https://sfplayhouse.org/2026-2027-season/rent/">
  <img src="https://sfplayhouse.org/wp-content/uploads/rent.jpg"/>
</a>
<a href="https://sfplayhouse.org/">Home</a>
</body></html>
"""

DETAIL_HTML = """
<html><head>
  <meta property="og:image" content="https://sfplayhouse.org/wp-content/uploads/peter-pan.png"/>
</head><body>
  <h1>Peter Pan Goes Wrong</h1>
  <p>Playing September 26 – November 28, 2026 at the Kensington Theatre.</p>
</body></html>
"""


def _detail_fixture() -> str:
    return (FIXTURES / "sfplayhouse_detail.html").read_text()


def _show() -> RawEvent:
    """A run-level show as produced by parse(), used to expand performances."""
    return RawEvent(
        title="Peter Pan Goes Wrong",
        start_time=datetime(2026, 9, 26, 19, 30, tzinfo=PACIFIC).astimezone(timezone.utc),
        location=VENUE,
        url="https://sfplayhouse.org/2026-2027-season/peter-pan-goes-wrong/",
        description="September 26 – November 28, 2026",
        image_url="https://sfplayhouse.org/wp-content/uploads/peter-pan.png",
    )


def test_matches():
    assert matches("https://sfplayhouse.org/")
    assert not matches("https://ybca.org/")


def test_parse_walks_show_urls(monkeypatch):
    fetches = []
    def fake_fetch(url):
        fetches.append(url)
        return DETAIL_HTML
    events = parse(HOME_HTML, fetch=fake_fetch)
    assert len(fetches) == 2  # 2 unique show urls
    assert len(events) == 2
    for ev in events:
        assert isinstance(ev, RawEvent)
        assert ev.title == "Peter Pan Goes Wrong"
        local = ev.start_time.astimezone(PACIFIC)
        assert (local.year, local.month, local.day) == (2026, 9, 26)
        assert ev.location == VENUE
        assert ev.image_url


def test_parse_range_single_day():
    assert _parse_range("September 26, 2026") == (date(2026, 9, 26), date(2026, 9, 26))


def test_parse_range_within_month():
    assert _parse_range("September 26 – 30, 2026") == (date(2026, 9, 26), date(2026, 9, 30))


def test_parse_range_cross_month():
    assert _parse_range("September 26 – November 28, 2026") == (date(2026, 9, 26), date(2026, 11, 28))


def test_parse_range_year_wrap():
    assert _parse_range("December 20 – January 3, 2028") == (date(2027, 12, 20), date(2028, 1, 3))


def test_parse_range_rejects_garbage():
    assert _parse_range("coming soon") is None


# --- per-performance expansion -------------------------------------------

def test_parse_performances_one_event_per_showing():
    events = parse_performances(_detail_fixture(), show=_show())
    assert len(events) == 3
    for ev in events:
        assert isinstance(ev, RawEvent)


def test_parse_performances_dates_and_times():
    events = parse_performances(_detail_fixture(), show=_show())
    locals_ = [ev.start_time.astimezone(PACIFIC) for ev in events]
    # local, tz-naive-looking startDate must be read as Pacific, converted to UTC
    assert (locals_[0].year, locals_[0].month, locals_[0].day,
            locals_[0].hour, locals_[0].minute) == (2026, 9, 25, 20, 0)
    assert (locals_[1].month, locals_[1].day, locals_[1].hour) == (9, 26, 20)
    # 2:00 PM matinee
    assert (locals_[2].month, locals_[2].day, locals_[2].hour, locals_[2].minute) == (9, 27, 14, 0)
    # stored as UTC
    for ev in events:
        assert ev.start_time.tzinfo == timezone.utc


def test_parse_performances_per_performance_url():
    events = parse_performances(_detail_fixture(), show=_show())
    urls = [ev.url for ev in events]
    assert urls == [
        "https://sfplayhouse.vbotickets.com/eventdate/peter_pan_goes_wrong/695538",
        "https://sfplayhouse.vbotickets.com/eventdate/peter_pan_goes_wrong/695539",
        "https://sfplayhouse.vbotickets.com/eventdate/peter_pan_goes_wrong/695540",
    ]
    assert len(set(urls)) == 3  # each showing has its own ticket URL


def test_parse_performances_passthrough_title_location_image():
    show = _show()
    events = parse_performances(_detail_fixture(), show=show)
    for ev in events:
        assert ev.title == show.title
        assert ev.location == show.location
        assert ev.image_url == show.image_url


def test_parse_performances_empty_when_no_event_graph():
    html = "<html><head><script type='application/ld+json'>{\"@type\":\"Product\"}</script></head><body></body></html>"
    assert parse_performances(html, show=_show()) == []
