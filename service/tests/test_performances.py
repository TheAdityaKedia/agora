from contextlib import contextmanager
from datetime import datetime, timezone

from scrapers.base import RawEvent
from scrapers.performances import expand_shows


def _show(title, url="https://x/show"):
    return RawEvent(
        title=title,
        start_time=datetime(2026, 9, 22, 19, tzinfo=timezone.utc),
        location="Venue",
        url=url,
        description="SEP 22-OCT 18, 2026",
    )


def _perf(title, day):
    return RawEvent(
        title=title,
        start_time=datetime(2026, 9, day, 2, tzinfo=timezone.utc),
        location="Venue",
        url=f"https://x/perf/{day}",
        description=None,
    )


@contextmanager
def _fake_browser():
    yield object()  # a dummy context; expand_one ignores it here


def test_expands_show_into_its_performances():
    show = _show("Hamlet")
    perfs = [_perf("Hamlet", 22), _perf("Hamlet", 23)]

    result = expand_shows([show], lambda ctx, s: perfs, label="test", _browser=_fake_browser)

    assert result == perfs


def test_falls_back_to_run_level_event_when_no_performances():
    show = _show("Hamlet")

    result = expand_shows([show], lambda ctx, s: [], label="test", _browser=_fake_browser)

    assert result == [show]


def test_mixed_expansion_and_fallback_preserves_order():
    a, b = _show("A", "https://x/a"), _show("B", "https://x/b")
    a_perfs = [_perf("A", 22), _perf("A", 23)]

    def expand_one(ctx, s):
        return a_perfs if s.title == "A" else []

    result = expand_shows([a, b], expand_one, label="test", _browser=_fake_browser)

    assert result == [*a_perfs, b]


def test_degrades_to_run_level_events_when_browser_fails():
    shows = [_show("A", "https://x/a"), _show("B", "https://x/b")]

    @contextmanager
    def broken_browser():
        raise RuntimeError("no chromium")
        yield  # pragma: no cover

    result = expand_shows(shows, lambda ctx, s: [], label="test", _browser=broken_browser)

    assert result == shows
