"""SF War Memorial calendar scraper (Herbst Theatre + Davies Symphony Hall).

The /calendar/ page is JS-rendered — needs Playwright. Each `.list-day`
carries:
  - .list-day-date ("Sat. September 5th") — no year; inferred from current
    SF year, rolled forward on Dec→Jan boundary
  - .list-event-details (one per event on that day) with:
      .details-name  — event title
      .details-by    — presenter/producer
      .detail-time   — "7:30PM"
      .venue_name    — "Herbst Theatre" / "Davies Symphony Hall" / etc.
      /event-detail/?eventId=... → detail link
      .list-day-right background-image → poster (site-relative)

The venue in `.venue_name` is more informative than the umbrella site URL,
so we surface it as the event's location.
"""
import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import RateLimited, browser_context, load_page_html


SOURCE = "sfwarmemorial.org"
NAME = "Herbst / Davies (SF War Memorial)"
BASE_URL = "https://sfwarmemorial.org"
EVENTS_URL = "https://sfwarmemorial.org/calendar/"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
WAIT_UNTIL = "load"
SETTLE_MS = 4000

_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], start=1
)}
# "Sat. September 5th" — day-of-week (short/long, optional period), month, ordinal day
_DAY_HEADER_RE = re.compile(r"^(?:[A-Za-z]+\.?,?\s+)?([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})?$")
_TIME_RE = re.compile(r"(\d{1,2}):?(\d{2})?\s*([APap][Mm])")
_BG_URL_RE = re.compile(r"url\(\s*(?:['\"])?([^'\"),\s]+)")


def matches(url: str) -> bool:
    return "sfwarmemorial.org" in url


def _parse_day_header(text: str, current_year: int) -> tuple[int, int, int | None]:
    """Parse 'Sat. September 5th' → (year, month, day) or None."""
    m = _DAY_HEADER_RE.match(" ".join(text.strip().split()))
    if not m:
        return None
    month = _MONTHS.get(m.group(1).title())
    if month is None:
        return None
    try:
        day = int(m.group(2))
    except ValueError:
        return None
    year = int(m.group(3)) if m.group(3) else current_year
    return year, month, day


def _parse_time(text: str) -> tuple[int, int]:
    m = _TIME_RE.search(text or "")
    if not m:
        return 19, 0  # 7:30-8 PM default for concert halls
    hour = int(m.group(1)) % 12
    minute = int(m.group(2)) if m.group(2) else 0
    if m.group(3).lower() == "pm":
        hour += 12
    return hour, minute


def _extract_event_image(details) -> str | None:
    right = details.select_one(".list-day-right")
    if not right:
        return None
    style = right.get("style") or ""
    m = _BG_URL_RE.search(style)
    if not m:
        return None
    return urljoin(BASE_URL, m.group(1))


def _parse_day(day_el, current_year: int) -> tuple[list[RawEvent], int]:
    header_tag = day_el.select_one(".list-day-date")
    header = _parse_day_header(header_tag.get_text(" ", strip=True) if header_tag else "", current_year)
    if not header:
        return [], current_year
    year, month, day_num = header
    events: list[RawEvent] = []
    for details in day_el.select(".list-event-details"):
        name_tag = details.select_one(".details-name")
        if not name_tag:
            continue
        title = name_tag.get_text(" ", strip=True)
        if not title:
            continue

        time_tag = details.select_one(".detail-time")
        hour, minute = _parse_time(time_tag.get_text(" ", strip=True)) if time_tag else (19, 0)

        venue_tag = details.select_one(".venue_name")
        venue = venue_tag.get_text(" ", strip=True) if venue_tag else "SF War Memorial"

        by_tag = details.select_one(".details-by")
        by = by_tag.get_text(" ", strip=True) if by_tag else None

        # Pick the detail-page link
        detail_a = None
        for a in details.find_all("a", href=True):
            if "/event-detail" in a["href"] or "eventId" in a["href"]:
                detail_a = a
                break
        href = detail_a.get("href") if detail_a else None
        # The href may contain a stray newline inside the value; strip it.
        if href:
            href = "".join(href.split())

        try:
            start_time = datetime(year, month, day_num, hour, minute, tzinfo=SOURCE_TZ).astimezone(timezone.utc)
        except ValueError:
            continue

        events.append(RawEvent(
            title=title,
            start_time=start_time,
            location=f"{venue}, San Francisco, CA",
            url=urljoin(BASE_URL, href) if href else None,
            description=by,
            image_url=_extract_event_image(details),
        ))
    return events, year


def parse(html: str) -> list[RawEvent]:
    soup = BeautifulSoup(html, "html.parser")
    events: list[RawEvent] = []
    year = datetime.now(SOURCE_TZ).year
    prev_month: int | None = None
    for day_el in soup.select(".list-day"):
        # Peek month for potential year rollover
        header_tag = day_el.select_one(".list-day-date")
        peek = _parse_day_header(
            header_tag.get_text(" ", strip=True) if header_tag else "", year,
        )
        if peek and prev_month is not None and peek[1] < prev_month:
            year += 1
        day_events, year = _parse_day(day_el, year)
        events.extend(day_events)
        if peek:
            prev_month = peek[1]
    return events


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    with browser_context() as context:
        try:
            html = load_page_html(context, url, wait_until=WAIT_UNTIL, settle_ms=SETTLE_MS)
        except RateLimited as e:
            print(f"[sfwarmemorial] blocked (HTTP {e.status}) at {e.url}, skipping", flush=True)
            return []
    return parse(html)
