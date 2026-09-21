"""American Conservatory Theater (A.C.T.) events scraper.

The what's-on page is plain server-rendered HTML — no headless browser needed.
Cards live under `.event-item` (buffer items with `.event-item--buffer` skipped).
Each card is a season show; the `<a>` container IS the event, carrying the
detail URL, title, and a date range.

Like ATG, cards represent multi-week runs with no per-performance times. We
emit one RawEvent per card, at 7:00 PM SF-local on the range's start day, and
preserve the full displayed range in the description.

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
from scrapers.browser import BROWSER_UA


SOURCE = "act-sf.org"
BASE_URL = "https://www.act-sf.org"
EVENTS_URL = "https://www.act-sf.org/whats-on"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")

VENUE = "A.C.T., 415 Geary St, San Francisco, CA 94102"

# Placeholder start-of-day time — A.C.T. cards don't expose showtimes; 7 PM
# SF-local is the typical curtain and gives calendar-sortable start times.
DEFAULT_HOUR = 19  # 7 PM

REQUEST_TIMEOUT = 20


def matches(url: str) -> bool:
    return "act-sf.org" in url


_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1
)}
# Any dash variant (ASCII, en-dash, em-dash).
_DASH_RE = re.compile(r"[–—-]")
_YEAR_TAIL_RE = re.compile(r",\s*(\d{4})\s*$")


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
    )


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


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    """Fetch A.C.T.'s what's-on page and parse it. Plain HTTP — no browser."""
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[act-sf] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    return parse(resp.text)
