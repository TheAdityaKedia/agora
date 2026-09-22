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
from scrapers.browser import RateLimited, browser_context, load_page_html


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


def _month_calendar_url(month: date) -> str:
    """Build the SFJAZZ per-month calendar URL for the first of `month`."""
    return f"{EVENTS_URL}?date={month.isoformat()}&layout=A"


def _next_month(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def scrape(url: str = EVENTS_URL, horizon: date | None = None) -> list[RawEvent]:
    """Walk SFJAZZ's per-month calendar from today to `horizon`.

    Stops early after `EMPTY_MONTH_STOP` consecutive months with zero events
    (SFJAZZ's programming has a fixed season end).
    """
    if horizon is None:
        horizon = date.today() + timedelta(days=LOOKAHEAD_DAYS)
    today = datetime.now(SOURCE_TZ).date()
    month_cursor = date(today.year, today.month, 1)
    seen_keys: set[tuple[str, datetime]] = set()
    events: list[RawEvent] = []
    empty_streak = 0
    while month_cursor <= horizon:
        month_url = _month_calendar_url(month_cursor)
        # Fresh browser context per month — reusing one context across many
        # month URLs seems to accumulate friction with Cloudflare, whereas a
        # cold context with its own JS-challenge round consistently succeeds.
        with browser_context() as context:
            try:
                html = load_page_html(
                    context, month_url,
                    wait_until=NETWORKIDLE_WAIT, settle_ms=SETTLE_MS,
                )
            except RateLimited as e:
                print(f"[sfjazz] blocked (HTTP {e.status}) at {e.url}, stopping early",
                      flush=True)
                break
        found = 0
        for ev in parse(html, base_year=month_cursor.year):
            key = (ev.title, ev.start_time)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            events.append(ev)
            found += 1
        if found == 0:
            empty_streak += 1
            if empty_streak >= EMPTY_MONTH_STOP:
                break
        else:
            empty_streak = 0
        month_cursor = _next_month(month_cursor)
        if month_cursor <= horizon:
            time.sleep(BETWEEN_MONTH_DELAY_S)
    return events
