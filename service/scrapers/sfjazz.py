"""SFJAZZ Center events scraper.

SFJAZZ sits behind Cloudflare with a 403 default for JS-less clients, so we
drive a headless browser. The "Ace Calendar" widget renders one
`.ace-cal-list-event` per performance:
  - .ace-cal-list-day-of-month ("Sep 20") — no year; inferred from current
    SF-local year and rolled forward at Dec→Jan month wraps.
  - h4 title inside a linked <a> → title + relative detail URL
  - .ace-cal-list-event-time ("3:00 PM | Miner Auditorium")
  - .ace-cal-list-event-image img[src] (relative → resolve)
"""
import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import RateLimited, browser_context, load_page_html


SOURCE = "sfjazz.org"
BASE_URL = "https://www.sfjazz.org"
EVENTS_URL = "https://www.sfjazz.org/calendar/"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "SFJAZZ Center, 201 Franklin St, San Francisco, CA 94102"

NETWORKIDLE_WAIT = "load"  # networkidle can hang on tracker beacons
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


def parse(html: str) -> list[RawEvent]:
    soup = BeautifulSoup(html, "html.parser")
    year = datetime.now(SOURCE_TZ).year
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


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    with browser_context() as context:
        try:
            html = load_page_html(context, url, wait_until=NETWORKIDLE_WAIT, settle_ms=SETTLE_MS)
        except RateLimited as e:
            print(f"[sfjazz] blocked (HTTP {e.status}) at {e.url}, skipping", flush=True)
            return []
    return parse(html)
