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


# --- load-more pagination ----------------------------------------------------

AJAX_FIXTURE = Path(__file__).parent / "fixtures" / "gamh_ajax_page.html"

# The real markup around the "Load more" button + nonce (trimmed from gamh.com).
PAGE_ONE_CHROME = """
<script id="seetickets-custom-scripts-js-extra">
var seetickets_ajax_obj = {"ajax_url":"https://gamh.com/wp-admin/admin-ajax.php","nonce":"6edb54e4d9"};
</script>
<button class="seetickets-load-more-btn" data-list-type="grid" data-see-ajax-page="2"
        data-see-total-pages="3">+ LOAD MORE EVENTS</button>
"""


def test_find_pagination_reads_nonce_pages_and_list_type():
    assert gamh.find_pagination(PAGE_ONE_CHROME) == ("6edb54e4d9", 3, "grid")


def test_find_pagination_without_button_means_single_page(html):
    assert gamh.find_pagination("<html><body>no button</body></html>") == (None, 1, "grid")


def test_ajax_fragment_parses_with_year_rollover(frozen_2026):
    """The fixture spans Dec -> Jan: January shows belong to next year."""
    events = parse(AJAX_FIXTURE.read_text())
    assert [(e.start_time.astimezone(PACIFIC).year, e.start_time.astimezone(PACIFIC).month)
            for e in events] == [(2026, 12), (2027, 1), (2027, 1)]


def test_pages_parsed_together_keep_rollover(html, frozen_2026):
    """Parsing page 1 + a later page in one pass keeps the year sequence right;
    parsing the later page alone would restart at the current year."""
    joined = parse(html + "\n" + AJAX_FIXTURE.read_text())
    jan = [e for e in joined if e.start_time.astimezone(PACIFIC).month == 1]
    assert jan and all(e.start_time.astimezone(PACIFIC).year == 2027 for e in jan)


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        pass


@pytest.fixture
def fake_session(monkeypatch, html):
    """Serve page 1 (fixture + pagination chrome) and ajax pages from a dict."""
    state = {"calls": [], "sleeps": [], "ajax": {}}

    class FakeSession:
        headers = {}

        def get(self, url, params=None, headers=None, timeout=None):
            state["calls"].append((url, dict(params or {})))
            if url == gamh.EVENTS_URL:
                return _Resp(html + PAGE_ONE_CHROME)
            return state["ajax"].get(params["seeAjaxPage"], _Resp(""))

    monkeypatch.setattr(gamh.requests, "Session", FakeSession)
    monkeypatch.setattr(gamh.time, "sleep", lambda s: state["sleeps"].append(s))
    return state


def test_scrape_follows_load_more_pages(fake_session, html, frozen_2026):
    fake_session["ajax"] = {2: _Resp(AJAX_FIXTURE.read_text()), 3: _Resp(AJAX_FIXTURE.read_text())}
    events = gamh.scrape()
    ajax_calls = [p for u, p in fake_session["calls"] if u == gamh.AJAX_URL]
    assert [p["seeAjaxPage"] for p in ajax_calls] == [2, 3]
    assert all(p["action"] == "get_seetickets_events" and p["nonce"] == "6edb54e4d9"
               and p["listType"] == "grid" for p in ajax_calls)
    assert len(events) == len(parse(html)) + 2 * len(parse(AJAX_FIXTURE.read_text()))


def test_scrape_honors_crawl_delay(fake_session, frozen_2026):
    fake_session["ajax"] = {2: _Resp(AJAX_FIXTURE.read_text()), 3: _Resp(AJAX_FIXTURE.read_text())}
    gamh.scrape()
    assert gamh.CRAWL_DELAY_S >= 3                      # robots.txt Crawl-delay: 3
    assert fake_session["sleeps"] == [gamh.CRAWL_DELAY_S] * 2


def test_scrape_stops_on_empty_or_failed_page(fake_session, html, frozen_2026):
    fake_session["ajax"] = {2: _Resp("", 500)}
    events = gamh.scrape()
    assert len(events) == len(parse(html))              # page 1 kept
    assert [p["seeAjaxPage"] for u, p in fake_session["calls"] if u == gamh.AJAX_URL] == [2]
