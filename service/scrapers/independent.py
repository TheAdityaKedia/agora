"""The Independent SF events scraper.

The calendar is a JS-rendered FullCalendar widget backed by Ticketweb. Each
`.fc-event` carries a compact `aria-label` in the form:
    "Title|YYYY-MM-DD|H:MM AM/PM"
which is our authoritative source for title, date, and time. The `href` is
a same-page hash pointing at a modal dialog — no public event URL exposed
from the calendar view, so RawEvent.url stays None.
"""
import re
from datetime import datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import RateLimited, browser_context, load_page_html


SOURCE = "theindependentsf.com"
NAME = "The Independent"
BASE_URL = "https://www.theindependentsf.com"
EVENTS_URL = "https://www.theindependentsf.com/calendar/"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "The Independent, 628 Divisadero St, San Francisco, CA 94117"

WAIT_UNTIL = "load"
SETTLE_MS = 4000

# "Sondre Lerche|2026-09-21|8:00 PM"
_ARIA_RE = re.compile(r"^\s*(?P<title>.+?)\|(?P<date>\d{4}-\d{2}-\d{2})\|(?P<time>\d{1,2}:\d{2}\s*[APap][Mm])\s*$")


def matches(url: str) -> bool:
    return "theindependentsf.com" in url


def _parse_aria(aria: str) -> tuple[str, datetime] | None:
    m = _ARIA_RE.match(aria or "")
    if not m:
        return None
    try:
        d = datetime.strptime(m.group("date"), "%Y-%m-%d").date()
    except ValueError:
        return None
    try:
        t = datetime.strptime(m.group("time").strip().upper().replace(" ", ""), "%I:%M%p").time()
    except ValueError:
        return None
    start_time = datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=SOURCE_TZ).astimezone(timezone.utc)
    return m.group("title").strip(), start_time


def _parse_event(el) -> RawEvent | None:
    parsed = _parse_aria(el.get("aria-label", ""))
    if not parsed:
        return None
    title, start_time = parsed

    img_tag = el.select_one("img.event-img") or el.find("img")
    image_url = urljoin(BASE_URL, img_tag["src"]) if img_tag and img_tag.get("src") else None

    # Doors / show times for context (start_time above is the show time).
    doors_tag = el.select_one(".tw-calendar-event-doors")
    doors = doors_tag.get_text(" ", strip=True) if doors_tag else None
    show_tag = el.select_one(".tw-calendar-event-time")
    show = show_tag.get_text(" ", strip=True) if show_tag else None
    description = " · ".join(b for b in (doors, show) if b) or None

    return RawEvent(
        title=title,
        start_time=start_time,
        location=VENUE,
        url=None,
        description=description,
        image_url=image_url,
    )


def parse(html: str) -> list[RawEvent]:
    soup = BeautifulSoup(html, "html.parser")
    return [ev for ev in (_parse_event(el) for el in soup.select(".fc-event")) if ev is not None]


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    with browser_context() as context:
        try:
            html = load_page_html(context, url, wait_until=WAIT_UNTIL, settle_ms=SETTLE_MS)
        except RateLimited as e:
            print(f"[independent] blocked (HTTP {e.status}) at {e.url}, skipping", flush=True)
            return []
    return parse(html)
