from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.sfplayhouse import parse, matches, _parse_range, VENUE

PACIFIC = ZoneInfo("America/Los_Angeles")


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
