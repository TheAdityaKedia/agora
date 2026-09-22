"""SFJAZZ Center events scraper.

SFJAZZ sits behind Cloudflare with a 403 default for JS-less clients, so we
drive a headless browser. The "Ace Calendar" widget renders one
`.ace-cal-list-event` per performance:
  - .ace-cal-list-day-of-month ("Sep 20") — no year; inferred from a base year
    (the month URL being scraped) and rolled forward at Dec→Jan month wraps.
  - h4 title inside a linked <a> → title + relative detail URL
  - .ace-cal-list-event-time ("3:00 PM | Miner Auditorium")
  - .ace-cal-list-event-image img[src] (relative → resolve)

The default `/calendar/` view shows only a short rolling window (~this week).
To cover the full season, `scrape()` walks the site's per-month endpoint
`/calendar/?date=YYYY-MM-01&layout=A` forward from today to the LOOKAHEAD
horizon, deduping within-run by (title, start_time).
"""
import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from config import LOOKAHEAD_DAYS
from scrapers.base import RawEvent
from scrapers.browser import (
    RateLimited, browser_context, browser_session, load_page_html,
)


SOURCE = "sfjazz.org"
NAME = "SFJAZZ Center"
BASE_URL = "https://www.sfjazz.org"
EVENTS_URL = "https://www.sfjazz.org/calendar/"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "SFJAZZ Center, 201 Franklin St, San Francisco, CA 94102"

NETWORKIDLE_WAIT = "load"  # networkidle can hang on tracker beacons
# Politeness delay between per-month calendar fetches; SFJAZZ is Cloudflare-
# fronted and 403s bursty traffic even with a warm context.
BETWEEN_MONTH_DELAY_S = 5.0
# Stop walking forward after this many consecutive empty months — SFJAZZ
# publishes through their season end and past that months are just empty.
EMPTY_MONTH_STOP = 2
# Detail-page fetches are Cloudflare-guarded and need a fresh context per URL
# (10-20s each including the JS challenge). Only enrich descriptions for events
# in the near-term window — farther-out events keep the "time | room" fallback
# and get real descriptions on later runs as their date approaches.
DESCRIPTION_WINDOW_DAYS = 60
BETWEEN_DETAIL_DELAY_S = 2.0
DETAIL_SETTLE_MS = 2000
# SFJAZZ's detail page shows this placeholder when a show has passed; skip it.
_EXPIRED_SHOW_MARKER = "performances for this production have passed"


def _log(msg: str) -> None:
    print(f"[sfjazz] {msg}", flush=True)
SETTLE_MS = 4000

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1
)}
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([APap][Mm])")


def matches(url: str) -> bool:
    return "sfjazz.org" in url


def _parse_month_day(text: str) -> tuple[int, int] | None:
    text = " ".join(text.strip().split())
    parts = text.split()
    if len(parts) != 2:
        return None
    month = _MONTHS.get(parts[0][:3].title())
    if month is None:
        return None
    try:
        return month, int(parts[1])
    except ValueError:
        return None


def _parse_time(text: str) -> tuple[int, int] | None:
    m = _TIME_RE.search(text or "")
    if not m:
        return None
    hour = int(m.group(1)) % 12
    if m.group(3).lower() == "pm":
        hour += 12
    return hour, int(m.group(2))


def _parse_event(el, year: int) -> tuple[RawEvent | None, int | None]:
    day_tag = el.select_one(".ace-cal-list-day-of-month")
    if not day_tag:
        return None, None
    md = _parse_month_day(day_tag.get_text(strip=True))
    if not md:
        return None, None
    month, day = md

    title_a = el.select_one(".ace-cal-list-event-details a") or el.find("a", href=True)
    title_tag = el.select_one(".ace-cal-list-event-details h4") or (title_a.find(["h3","h4","h5"]) if title_a else None)
    title = title_tag.get_text(strip=True) if title_tag else (title_a.get_text(strip=True) if title_a else None)
    if not title:
        return None, month
    href = title_a.get("href") if title_a else None
    url = urljoin(BASE_URL, href) if href else None

    time_tag = el.select_one(".ace-cal-list-event-time")
    time_text = time_tag.get_text(" ", strip=True) if time_tag else ""
    hm = _parse_time(time_text) or (19, 0)  # 7 PM SFJAZZ default if missing
    hour, minute = hm

    # Venue detail (Miner Auditorium, Joe Henderson Lab, …) sits after "|"
    venue_extra = None
    if time_tag:
        spans = time_tag.find_all("span")
        if spans:
            venue_extra = spans[-1].get_text(" ", strip=True) or None
    location = f"{VENUE} — {venue_extra}" if venue_extra else VENUE

    img_tag = el.select_one(".ace-cal-list-event-image-img") or el.select_one(".ace-cal-list-event-image img")
    image_url = urljoin(BASE_URL, img_tag["src"]) if img_tag and img_tag.get("src") else None

    try:
        start_time = datetime(year, month, day, hour, minute, tzinfo=SOURCE_TZ).astimezone(timezone.utc)
    except ValueError:
        return None, month

    return RawEvent(
        title=title,
        start_time=start_time,
        location=location,
        url=url,
        description=time_text or None,
        image_url=image_url,
    ), month


def parse(html: str, base_year: int | None = None) -> list[RawEvent]:
    """Parse an SFJAZZ calendar page into RawEvents.

    `base_year` is the year of the month being scraped — critical when
    walking `/calendar/?date=YYYY-MM-01` for future months, since day cells
    only carry short "Sep 20"-style labels with no year. Defaults to the
    current SF-local year for backwards compatibility.
    """
    soup = BeautifulSoup(html, "html.parser")
    year = base_year if base_year is not None else datetime.now(SOURCE_TZ).year
    prev_month: int | None = None
    events: list[RawEvent] = []
    for el in soup.select(".ace-cal-list-event"):
        # Peek month for year rollover
        day_tag = el.select_one(".ace-cal-list-day-of-month")
        peek = _parse_month_day(day_tag.get_text(strip=True)) if day_tag else None
        if peek and prev_month is not None and peek[0] < prev_month:
            year += 1
        ev, month = _parse_event(el, year)
        if ev is not None:
            events.append(ev)
        if month is not None:
            prev_month = month
    return events


def parse_detail_description(html: str) -> str | None:
    """Return the show's description blurb from an SFJAZZ detail page, or None.

    SFJAZZ places the descriptive copy in the first `.rich-text` block on the
    page. Later `.rich-text` blocks are personnel lists, address+phone, cookie
    banner, etc. For expired shows the first block is a placeholder — skip.
    """
    soup = BeautifulSoup(html, "html.parser")
    for block in soup.select(".rich-text"):
        text = block.get_text(" ", strip=True)
        if not text or len(text) < 60:
            continue
        if _EXPIRED_SHOW_MARKER in text.lower():
            continue
        return text
    return None


def _month_calendar_url(month: date) -> str:
    """Build the SFJAZZ per-month calendar URL for the first of `month`."""
    return f"{EVENTS_URL}?date={month.isoformat()}&layout=A"


def _next_month(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def _url_slug(u: str) -> str:
    """Short label for progress logs: strip domain + trailing slash."""
    tail = u.rsplit("/", 2)
    return tail[-2] if u.endswith("/") and len(tail) >= 2 else tail[-1]


def scrape(url: str = EVENTS_URL, horizon: date | None = None) -> list[RawEvent]:
    """Walk SFJAZZ's per-month calendar from today to `horizon`, then fetch
    each near-term event's detail page for the real blurb.

    Description enrichment is bounded to events within DESCRIPTION_WINDOW_DAYS
    (default 60) — farther-out events keep the "time | room" fallback and get
    real descriptions on later runs as their date approaches.

    Stops the month walk early after `EMPTY_MONTH_STOP` consecutive empty
    months. Cloudflare rejects consecutive same-context fetches, so we
    amortize the browser launch via `browser_session()` and open a fresh
    context per URL.
    """
    if horizon is None:
        horizon = date.today() + timedelta(days=LOOKAHEAD_DAYS)
    today = datetime.now(SOURCE_TZ).date()
    month_cursor = date(today.year, today.month, 1)
    seen_keys: set[tuple[str, datetime]] = set()
    events: list[RawEvent] = []
    empty_streak = 0

    with browser_session() as session:
        # --- Phase 1: walk months, collect events ---
        _log(f"phase 1: walking months from {month_cursor} to horizon {horizon}")
        month_index = 0
        while month_cursor <= horizon:
            month_index += 1
            month_url = _month_calendar_url(month_cursor)
            t0 = time.monotonic()
            _log(f"month {month_index} {month_cursor}: fetching")
            try:
                with session.fresh_context() as ctx:
                    html = load_page_html(
                        ctx, month_url,
                        wait_until=NETWORKIDLE_WAIT, settle_ms=SETTLE_MS,
                    )
            except RateLimited as e:
                _log(f"month {month_index} {month_cursor}: blocked (HTTP {e.status}), stopping walk")
                break
            found = 0
            for ev in parse(html, base_year=month_cursor.year):
                key = (ev.title, ev.start_time)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                events.append(ev)
                found += 1
            _log(f"month {month_index} {month_cursor}: {found} new events in {time.monotonic() - t0:.1f}s")
            if found == 0:
                empty_streak += 1
                if empty_streak >= EMPTY_MONTH_STOP:
                    _log(f"stopping: {empty_streak} consecutive empty months")
                    break
            else:
                empty_streak = 0
            month_cursor = _next_month(month_cursor)
            if month_cursor <= horizon:
                time.sleep(BETWEEN_MONTH_DELAY_S)

        # --- Phase 2: enrich descriptions for near-term unique URLs ---
        window_end = datetime.now(timezone.utc) + timedelta(days=DESCRIPTION_WINDOW_DAYS)
        urls_in_window: set[str] = {
            ev.url for ev in events
            if ev.url and ev.start_time <= window_end
        }
        unique_urls = sorted(urls_in_window)
        _log(f"phase 2: enriching descriptions for {len(unique_urls)} unique URLs "
             f"(events within next {DESCRIPTION_WINDOW_DAYS} days)")
        descriptions: dict[str, str] = {}
        for i, detail_url in enumerate(unique_urls, start=1):
            slug = _url_slug(detail_url)
            _log(f"detail {i}/{len(unique_urls)} {slug}: fetching")
            t0 = time.monotonic()
            try:
                with session.fresh_context() as ctx:
                    html = load_page_html(
                        ctx, detail_url,
                        wait_until=NETWORKIDLE_WAIT, settle_ms=DETAIL_SETTLE_MS,
                    )
            except RateLimited as e:
                _log(f"detail {i}/{len(unique_urls)} {slug}: blocked (HTTP {e.status}), stopping phase 2")
                break
            except Exception as e:
                _log(f"detail {i}/{len(unique_urls)} {slug}: error {type(e).__name__}: {e}")
                continue
            load_s = time.monotonic() - t0
            desc = parse_detail_description(html)
            if desc:
                descriptions[detail_url] = desc
                _log(f"detail {i}/{len(unique_urls)} {slug}: description {len(desc)} chars ({load_s:.1f}s)")
            else:
                _log(f"detail {i}/{len(unique_urls)} {slug}: no description found ({load_s:.1f}s)")
            if i < len(unique_urls):
                time.sleep(BETWEEN_DETAIL_DELAY_S)

    # Apply fetched descriptions in-place, keeping the "time | room" fallback.
    enriched = 0
    for ev in events:
        if ev.url and ev.url in descriptions:
            ev.description = descriptions[ev.url]
            enriched += 1
    _log(f"done: {len(events)} events collected, {enriched} enriched with detail-page descriptions")
    return events
