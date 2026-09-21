"""Shared orchestration for expanding season-show listings into performances.

Several theater sources (A.C.T., ATG, Berkeley Rep, …) list a *show* per card —
a multi-week run with only a date range — while the individual showtimes live on
each show's detail/performances page, behind a source-specific ticketing system
(Tessitura, ATG's own, Vendini, …). Each scraper supplies its own function to
render and parse one show's performances; this helper drives the common loop:

  for each run-level show:
    performances = expand_one(ctx, show)   # source-specific
    use them, or fall back to the run-level show if there are none

so a show is never dropped, and the whole thing degrades to run-level events if
the headless browser is unavailable.
"""
from collections.abc import Callable, Sequence

from scrapers.base import RawEvent
from scrapers.browser import browser_context


def expand_shows(
    shows: Sequence[RawEvent],
    expand_one: Callable[[object, RawEvent], list[RawEvent]],
    *,
    label: str,
    _browser=browser_context,
) -> list[RawEvent]:
    """Expand run-level `shows` into per-performance events.

    `expand_one(ctx, show)` renders and parses one show's performances using the
    shared browser context `ctx`; returning an empty list means "no performances
    found" and the run-level `show` is kept as a fallback. If the browser context
    can't be created (e.g. Chromium missing), all shows degrade to run-level.
    `_browser` is injectable for testing.
    """
    events: list[RawEvent] = []
    try:
        with _browser() as ctx:
            for show in shows:
                performances = expand_one(ctx, show)
                events.extend(performances if performances else [show])
    except Exception as e:  # browser unavailable/crash — degrade gracefully
        print(f"[{label}] performance expansion failed ({e}); using run-level events", flush=True)
        return list(shows)
    return events
