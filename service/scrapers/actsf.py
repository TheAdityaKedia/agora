"""American Conservatory Theater (A.C.T.) events scraper.

The what's-on page is plain server-rendered HTML — no headless browser needed.
Cards live under `.event-item` (buffer items with `.event-item--buffer` skipped).
Each card is a season show; the `<a>` container IS the event, carrying the
detail URL, title, and a date range.

The listing shows only a run range, not individual showtimes. Those live on
each show's `/performances` page — a Tessitura/Vue widget rendered client-side —
so `scrape()` renders that page in a headless browser and emits one RawEvent per
performance (see `parse_performances`), each with its own showtime and ticket
URL. When a show's performances can't be fetched (no detail URL, unparseable
range, browser error, or a page with no rows — e.g. limited engagements), we
fall back to a single run-level event at 7:00 PM SF-local on the range's start
day, so a show is never dropped.

Date formats seen (all with uppercase 3-letter month, year always at the end):
  - Cross-month range: "SEP 22–OCT 18, 2026" (en-dash), "NOV 12—DEC 6, 2026"
    (em-dash), "MAY 13-JUN 13, 2027" (ASCII hyphen)
  - Same-month range: "MAR 10-27, 2027" (right side is day-only)
  - Single day: "OCT 21, 2026"

A.C.T. cards don't carry a per-event venue — all shows play at their Toni
Rembe Theater (with some at the Strand), which isn't distinguishable from the
listing markup. We use the primary venue as the location.
"""
import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA, RateLimited, load_page_html
from scrapers.performances import expand_shows


SOURCE = "act-sf.org"
NAME = "A.C.T. (American Conservatory Theater)"
BASE_URL = "https://www.act-sf.org"
EVENTS_URL = "https://www.act-sf.org/whats-on"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")

VENUE = "A.C.T., 415 Geary St, San Francisco, CA 94102"

# Placeholder start-of-day time — A.C.T. cards don't expose showtimes; 7 PM
# SF-local is the typical curtain and gives calendar-sortable start times.
DEFAULT_HOUR = 19  # 7 PM

REQUEST_TIMEOUT = 20

# The /performances widget renders its rows client-side from Tessitura; give it
# a moment to fetch and paint after navigation.
PERF_SETTLE_MS = 6000


def matches(url: str) -> bool:
    return "act-sf.org" in url


_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1
)}
# Any dash variant (ASCII, en-dash, em-dash).
_DASH_RE = re.compile(r"[–—-]")
_YEAR_TAIL_RE = re.compile(r",\s*(\d{4})\s*$")
# Performance-row clock time, e.g. "06:30PM" (no space) or "2:00 PM".
_PERF_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([APap][Mm])")


def _parse_month_day(text: str) -> tuple[int, int] | None:
    parts = text.strip().split()
    if len(parts) != 2:
        return None
    month = _MONTHS.get(parts[0].upper()[:3])
    if month is None:
        return None
    try:
        return month, int(parts[1])
    except ValueError:
        return None


def _parse_date_range(text: str) -> tuple[date, date] | None:
    """Parse an A.C.T. date string into (start_date, end_date), or None."""
    text = " ".join(text.strip().split())  # collapse whitespace
    if not text:
        return None
    normalized = _DASH_RE.sub("-", text)
    m = _YEAR_TAIL_RE.search(normalized)
    if not m:
        return None
    year = int(m.group(1))
    body = normalized[: m.start()].strip()

    parts = body.split("-", 1)
    if len(parts) == 1:
        md = _parse_month_day(parts[0])
        if not md:
            return None
        d = date(year, md[0], md[1])
        return d, d

    left, right = parts[0].strip(), parts[1].strip()
    left_md = _parse_month_day(left)
    if not left_md:
        return None
    start = date(year, left_md[0], left_md[1])

    if " " in right:
        right_md = _parse_month_day(right)
        if not right_md:
            return None
        end = date(year, right_md[0], right_md[1])
    else:
        try:
            end = date(year, left_md[0], int(right))
        except ValueError:
            return None

    # Cross-year run given as "DEC 20-JAN 5, 2028" would parse start after end;
    # roll start back a year in that case.
    if start > end:
        try:
            start = start.replace(year=start.year - 1)
        except ValueError:
            return None
    return start, end


def _parse_event_item(item) -> RawEvent | None:
    title_tag = item.select_one(".event-item__title")
    date_tag = item.select_one(".event-item__date")
    if not (title_tag and date_tag):
        return None
    title = title_tag.get_text(strip=True)
    date_text = date_tag.get_text(" ", strip=True)
    range_ = _parse_date_range(date_text)
    if not range_:
        return None
    start_day, _end_day = range_

    href = item.get("href")
    url = urljoin(BASE_URL, href) if href else None

    img_tag = item.select_one("img.event-item__image") or item.find("img")
    image_url = urljoin(BASE_URL, img_tag["src"]) if img_tag and img_tag.get("src") else None

    start_time = datetime(
        start_day.year, start_day.month, start_day.day, DEFAULT_HOUR, 0,
        tzinfo=SOURCE_TZ,
    ).astimezone(timezone.utc)

    return RawEvent(
        title=title,
        start_time=start_time,
        location=VENUE,
        url=url,
        description=date_text,
        image_url=image_url,
    )


def _parse_perf_time(text: str) -> tuple[int, int] | None:
    m = _PERF_TIME_RE.search(text or "")
    if not m:
        return None
    hour = int(m.group(1)) % 12
    if m.group(3).lower() == "pm":
        hour += 12
    return hour, int(m.group(2))


def _parse_perf_date(text: str) -> tuple[int, int] | None:
    """Parse a performance-row date like 'Tue Sep 22' into (month, day)."""
    parts = text.strip().split()
    if len(parts) < 2:
        return None
    month = _MONTHS.get(parts[-2].upper()[:3])
    if month is None:
        return None
    try:
        return month, int(parts[-1])
    except ValueError:
        return None


def _infer_perf_year(month: int, day: int, run_start: date, run_end: date) -> int:
    """Performance rows carry no year; pick the one landing inside the run.

    Handles a Dec→Jan run where the run spans two calendar years.
    """
    for year in (run_start.year, run_end.year):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if run_start <= d <= run_end:
            return year
    return run_start.year


def parse_performances(
    html: str,
    *,
    title: str,
    run_start: date,
    run_end: date,
    location: str,
    image_url: str | None,
) -> list[RawEvent]:
    """Parse a show's rendered `/performances` page into one RawEvent per showing.

    The page is a Tessitura/Vue widget; each `.performance-list__item` carries a
    `.date` ("Tue Sep 22") and `.time` ("06:30PM") with no year — inferred from
    the show's run range — plus a unique per-performance ticketing URL.
    """
    soup = BeautifulSoup(html, "html.parser")
    events: list[RawEvent] = []
    for item in soup.select(".performance-list__item"):
        dt = item.select_one(".performance__date-time")
        if not dt:
            continue
        date_span = dt.select_one(".date")
        time_span = dt.select_one(".time")
        if not (date_span and time_span):
            continue
        md = _parse_perf_date(date_span.get_text(strip=True))
        hm = _parse_perf_time(time_span.get_text(strip=True))
        if not (md and hm):
            continue
        month, day = md
        hour, minute = hm
        year = _infer_perf_year(month, day, run_start, run_end)
        try:
            start_time = datetime(
                year, month, day, hour, minute, tzinfo=SOURCE_TZ,
            ).astimezone(timezone.utc)
        except ValueError:
            continue

        a = item.select_one("a.performance[href]") or item.select_one("a[href]")
        url = a.get("href") if a else None

        keywords = [k.get_text(strip=True) for k in item.select(".performance__keyword")]
        note_tag = item.select_one(".performance__night-content")
        note = note_tag.get_text(strip=True) if note_tag else None
        desc_parts = [*keywords, *( [note] if note else [] )]
        description = " · ".join(p for p in desc_parts if p) or None

        events.append(RawEvent(
            title=title,
            start_time=start_time,
            location=location,
            url=url,
            description=description,
            image_url=image_url,
        ))
    return events


def parse(html: str) -> list[RawEvent]:
    """Parse the A.C.T. what's-on page into RawEvents (one per show)."""
    soup = BeautifulSoup(html, "html.parser")
    events: list[RawEvent] = []
    for item in soup.select(".event-item"):
        if "event-item--buffer" in (item.get("class") or []):
            continue
        ev = _parse_event_item(item)
        if ev is not None:
            events.append(ev)
    return events


def _scrape_show_performances(ctx, show: RawEvent) -> list[RawEvent]:
    """Render one show's `/performances` page and parse its showings.

    Returns [] when the show has no detail URL, an unparseable run range, or the
    page renders no performance rows — the caller falls back to the run-level
    event in that case.
    """
    if not show.url:
        return []
    range_ = _parse_date_range(show.description or "")
    if not range_:
        return []
    run_start, run_end = range_
    perf_url = show.url.rstrip("/") + "/performances"
    try:
        html = load_page_html(ctx, perf_url, wait_until="load", settle_ms=PERF_SETTLE_MS)
    except RateLimited as e:
        print(f"[act-sf] blocked (HTTP {e.status}) at {e.url}, skipping performances", flush=True)
        return []
    return parse_performances(
        html,
        title=show.title,
        run_start=run_start,
        run_end=run_end,
        location=show.location,
        image_url=show.image_url,
    )


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    """Fetch A.C.T.'s what's-on listing, then expand each show to its showings.

    The listing is plain server-rendered HTML (one card per show). Each card's
    `/performances` page is a Tessitura/Vue widget, so we render it in a headless
    browser and emit one event per performance. If the performances can't be
    fetched (browser error) or a show has none, we fall back to the run-level
    event so a show is never dropped.
    """
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[act-sf] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    shows = parse(resp.text)
    return expand_shows(shows, _scrape_show_performances, label="act-sf")
