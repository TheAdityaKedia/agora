from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import greatstar
from scrapers.greatstar import (
    parse,
    parse_performances,
    matches,
    _parse_date_range,
    _scrape_show_performances,
    DEFAULT_HOUR,
)
from scrapers.browser import RateLimited

FIXTURES = Path(__file__).parent / "fixtures"
LISTING_FIXTURE = FIXTURES / "greatstar_events.html"
DETAIL_FIXTURE = FIXTURES / "greatstar_tickettailor.html"
UTC = ZoneInfo("UTC")
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return LISTING_FIXTURE.read_text()


@pytest.fixture
def detail_html():
    return DETAIL_FIXTURE.read_text()


def _show():
    return RawEvent(
        title="The Umbilical Brothers",
        start_time=datetime(2026, 9, 25, DEFAULT_HOUR, tzinfo=PACIFIC),
        location=greatstar.VENUE,
        url="https://www.tickettailor.com/events/nx5theatricalllc/2312766",
        description="September 25 - 26, 2026 · The Umbilical Brothers are an international comedy phenomenon.",
        image_url="https://www.greatstartheater.org/shows/poster.png",
    )


# --- Listing (run-level) parsing ---

class _FakeCtx:
    def __init__(self):
        self.browser = "browser"
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """PERF_DELAY_S is a real sleep; record it instead of waiting in tests."""
    sleeps = []
    monkeypatch.setattr(greatstar.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def test_matches():
    assert matches("https://www.greatstartheater.org/whats-playing")
    assert not matches("https://magictheatre.org/")


def test_parse_returns_run_level_events(html):
    events = parse(html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)
    assert events[0].title == "The Umbilical Brothers"
    # Third-party TicketTailor href is hotlinked as-is
    assert events[0].url == "https://www.tickettailor.com/events/nx5theatricalllc/2312766"
    assert events[0].location == greatstar.VENUE


def test_parse_date_range_single_day():
    assert _parse_date_range("September 28, 2026") == date(2026, 9, 28)


def test_parse_date_range_short_range_uses_start():
    assert _parse_date_range("September 25 - 26, 2026") == date(2026, 9, 25)


# --- Per-performance parsing (TicketTailor JSON-LD Event blocks) ---

def test_parse_performances_one_event_per_occurrence(detail_html):
    events = parse_performances(detail_html, show=_show())
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_performances_extracts_date_and_time(detail_html):
    """Occurrence startDate '2026-09-25T19:00:00-07:00' → exact UTC, no year guessing."""
    events = parse_performances(detail_html, show=_show())
    assert events[0].start_time.astimezone(UTC) == datetime(2026, 9, 26, 2, 0, tzinfo=UTC)
    assert events[1].start_time.astimezone(UTC) == datetime(2026, 9, 27, 2, 0, tzinfo=UTC)
    # SF-local both at 7 PM
    assert events[0].start_time.astimezone(PACIFIC).hour == 19


def test_parse_performances_uses_show_level_url_not_offer_deeplink(detail_html):
    """Every performance shares the card's stable show-level landing URL, not the
    per-occurrence ticket deep link (offer URLs carry a differing ?date_id=...)."""
    show = _show()
    events = parse_performances(detail_html, show=show)
    assert [e.url for e in events] == [show.url, show.url]
    assert all("date_id" not in (e.url or "") for e in events)


def test_parse_performances_carries_show_fields(detail_html):
    show = _show()
    ev = parse_performances(detail_html, show=show)[0]
    assert ev.title == show.title
    assert ev.image_url == show.image_url
    assert ev.description == show.description
    assert ev.location == show.location


def test_parse_performances_empty_when_no_event_jsonld():
    assert parse_performances("<html><body>no json-ld</body></html>", show=_show()) == []


# --- _scrape_show_performances (browser + fallback wiring) ---

def test_scrape_show_performances_no_url_returns_empty():
    show = _show()
    show.url = None
    assert _scrape_show_performances(object(), show) == []


def test_scrape_show_performances_renders_and_parses(monkeypatch, detail_html):
    monkeypatch.setattr(greatstar, "load_page_html", lambda ctx, url, **kw: detail_html)
    events = _scrape_show_performances(object(), _show())
    assert len(events) == 2


def test_scrape_show_performances_rate_limited_returns_empty(monkeypatch):
    """Blocked on both the first try and the fresh-context retry → []."""
    def boom(ctx, url, **kw):
        raise RateLimited(url, 403)
    monkeypatch.setattr(greatstar, "load_page_html", boom)
    monkeypatch.setattr(greatstar, "new_browser_context", lambda browser: _FakeCtx())
    assert _scrape_show_performances(_FakeCtx(), _show()) == []


def test_scrape_show_performances_empty_html_falls_back_to_empty(monkeypatch):
    """A non-TicketTailor page (no Event JSON-LD) yields [] so the caller keeps the run-level show."""
    monkeypatch.setattr(greatstar, "load_page_html", lambda ctx, url, **kw: "<html></html>")
    assert _scrape_show_performances(object(), _show()) == []



# --- throttling: delay + fresh-context retry --------------------------------


def test_scrape_show_performances_waits_before_render(monkeypatch, detail_html, no_sleep):
    monkeypatch.setattr(greatstar, "load_page_html", lambda ctx, url, **kw: detail_html)
    _scrape_show_performances(_FakeCtx(), _show())
    assert no_sleep == [greatstar.PERF_DELAY_S]


def test_scrape_show_performances_retries_once_in_fresh_context(monkeypatch, detail_html, no_sleep):
    from scrapers.browser import RateLimited

    original, fresh = _FakeCtx(), _FakeCtx()
    seen = []

    def load(ctx, url, **kw):
        seen.append(ctx)
        if ctx is original:
            raise RateLimited(url, 403)
        return detail_html

    monkeypatch.setattr(greatstar, "load_page_html", load)
    monkeypatch.setattr(greatstar, "new_browser_context", lambda browser: fresh)
    events = _scrape_show_performances(original, _show())
    assert seen == [original, fresh]
    assert len(events) > 1           # performances parsed from the retry
    assert fresh.closed              # the retry context doesn't leak


def test_scrape_show_performances_persistent_block_falls_back(monkeypatch, no_sleep):
    from scrapers.browser import RateLimited

    fresh = _FakeCtx()

    def load(ctx, url, **kw):
        raise RateLimited(url, 403)

    monkeypatch.setattr(greatstar, "load_page_html", load)
    monkeypatch.setattr(greatstar, "new_browser_context", lambda browser: fresh)
    assert _scrape_show_performances(_FakeCtx(), _show()) == []
    assert fresh.closed


def test_scrape_uses_full_chromium(monkeypatch):
    """scrape() hands expand_shows a full-Chromium browser factory."""
    captured = {}

    class Resp:
        status_code = 200
        text = "<html></html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(greatstar.requests, "get", lambda *a, **k: Resp())
    monkeypatch.setattr(greatstar, "parse", lambda html: [])
    monkeypatch.setattr(greatstar, "browser_context",
                        lambda **kw: captured.setdefault("kw", kw))

    def fake_expand(shows, fn, *, label, _browser):
        _browser()
        return []

    monkeypatch.setattr(greatstar, "expand_shows", fake_expand)
    greatstar.scrape()
    assert captured["kw"] == {"full_chromium": True}
