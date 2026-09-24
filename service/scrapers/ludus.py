"""Shared Ludus (ludus.com) ticketing-calendar scraping helpers.

Small theaters that sell tickets through Ludus embed a calendar at
``<org>.ludus.com/calendar``. The page blocks plain requests (403) but renders
in a real browser, and it ships every showtime as an escaped JSON array inside
an Alpine.js ``x-data="ludusCalendar(JSON.parse('[…]'), …)"`` attribute. Each
showtime carries title, date, time, category (venue), status, and a show-page
URL — so it's a clean per-performance source. Any Ludus venue plugs in with a
thin wrapper (see scrapers/themarsh.py) supplying its calendar URL.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from scrapers.base import RawEvent
from scrapers.browser import RateLimited, browser_context, load_page_html


SOURCE_TZ = ZoneInfo("America/Los_Angeles")
# The events array is the first JSON.parse arg of the ludusCalendar() component.
_EVENTS_RE = re.compile(r"ludusCalendar\(JSON\.parse\('(.+?)'\)\s*,\s*JSON\.parse\(", re.DOTALL)


def _log(msg: str) -> None:
    print(f"[ludus] {msg}", flush=True)


def _extract_events_json(page_html: str) -> list[dict]:
    """Pull and decode the embedded showtime array from the calendar HTML."""
    m = _EVENTS_RE.search(page_html)
    if not m:
        return []
    # The blob is a JS string literal where the structural quotes are ",
    # so decode the string escapes first (" -> ", \/ -> /), then JSON-parse.
    raw = m.group(1).replace("\\'", "'")
    try:
        decoded = html.unescape(json.loads('"' + raw + '"'))
        data = json.loads(decoded)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _parse_start(date: str | None, time_str: str | None) -> datetime | None:
    if not (date and time_str):
        return None
    try:
        naive = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %I:%M %p")
    except ValueError:
        return None
    return naive.replace(tzinfo=SOURCE_TZ).astimezone(timezone.utc)


def parse_calendar(page_html: str, *, base_url: str, fallback_location: str | None = None) -> list[RawEvent]:
    """Parse a Ludus calendar page into RawEvents (one per showtime).

    Pure — no network — so it's testable against a captured page. Skips past
    showtimes (the blob includes them with ``status == "past"``).
    """
    origin = re.match(r"https?://[^/]+", base_url)
    origin = origin.group(0) if origin else ""
    events: list[RawEvent] = []
    for st in _extract_events_json(page_html):
        if st.get("status") == "past":
            continue
        title = (st.get("title") or "").strip()
        start_time = _parse_start(st.get("date"), st.get("time"))
        if not (title and start_time):
            continue
        share = st.get("shareUrl") or ""
        if share.startswith("/"):
            share = origin + share
        events.append(RawEvent(
            title=title,
            start_time=start_time,
            location=(st.get("categoryName") or "").strip() or fallback_location,
            url=share or None,
            description=None,
            image_url=None,
        ))
    return events


def scrape_calendar(calendar_url: str, *, fallback_location: str | None = None) -> list[RawEvent]:
    """Render a Ludus calendar in a browser (it 403s plain requests) and parse it."""
    try:
        with browser_context() as ctx:
            page_html = load_page_html(ctx, calendar_url, wait_until="load", settle_ms=1500)
    except RateLimited as e:
        _log(f"blocked (HTTP {e.status}) at {e.url}")
        return []
    except Exception as e:  # browser/launch failures shouldn't abort the whole run
        _log(f"render failed for {calendar_url}: {type(e).__name__}: {e}")
        return []
    events = parse_calendar(page_html, base_url=calendar_url, fallback_location=fallback_location)
    _log(f"{calendar_url}: {len(events)} upcoming showtimes")
    return events
