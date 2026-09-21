"""Black Bird Bookstore events scraper.

Black Bird's events page (a Shopify site using the Mahina Events app) renders
a list of 12 upcoming events client-side and reveals the rest via a
"Load More Events" button that appends 12 more each click. Events have no
per-event detail URL — the block is just markup, no anchor — so we dedupe
on title + start_time (RawEvent.url is None).

Individual event blocks carry the day and short month ("22", "Sep") but no
year. We infer the year by scanning events in list order (they're already
sorted ascending) and rolling the year forward whenever the month goes
backward, starting from the current SF-local year.
"""
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from config import LOOKAHEAD_DAYS
from scrapers.base import RawEvent
from scrapers.browser import RateLimited, browser_context, load_page_html


SOURCE = "blackbirdsf.com"
NAME = "Black Bird Bookstore"
BASE_URL = "https://blackbirdsf.com"
EVENTS_URL = "https://blackbirdsf.com/pages/events"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
STORE_ADDRESS = "Black Bird Bookstore, 4033 Judah St, San Francisco, CA 94122"

# Rendered by JS; wait for the network to quiet down and give React a beat.
NETWORKIDLE = "networkidle"
SETTLE_MS = 2500

# Load-More pagination: click, wait, parse, repeat. Cap to prevent runaway.
LOAD_MORE_LABEL = "Load More Events"
LOAD_MORE_MAX_CLICKS = 12  # ~144 events max — well past 12-month horizon
LOAD_MORE_WAIT_MS = 2000

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1
)}

# `.ma-image` carries the poster as an inline `background-image: url("...")`.
# Some blocks include a spurious trailing `)` inside the URL (a bug in the
# source page) — strip it.
_BG_IMAGE_RE = re.compile(r'url\(\s*["\']([^"\']+)["\']\s*\)')


def _extract_image_url(block) -> str | None:
    img_div = block.select_one(".ma-image")
    if not img_div:
        return None
    style = img_div.get("style") or ""
    m = _BG_IMAGE_RE.search(style)
    if not m:
        return None
    url = m.group(1).rstrip(")")
    return url or None


def matches(url: str) -> bool:
    return "blackbirdsf.com" in url


def _parse_event(block, current_year: int) -> tuple[RawEvent | None, int, int | None]:
    """Parse one .ma-event block.

    Returns (event, year_used, month_num) so the caller can advance year
    across month rollovers.
    """
    title_tag = block.select_one(".ma-title")
    day_tag = block.select_one(".ma-day__date")
    mon_tag = block.select_one(".ma-day__month")
    time_tag = block.select_one("[ma-start-time]")
    if not (title_tag and day_tag and mon_tag and time_tag):
        return None, current_year, None

    title = title_tag.get_text(strip=True)
    day_str = day_tag.get_text(strip=True)
    mon_str = mon_tag.get_text(strip=True)
    time_str = time_tag.get_text(strip=True)

    month_num = _MONTHS.get(mon_str[:3])
    if month_num is None:
        return None, current_year, None

    try:
        naive = datetime.strptime(f"{day_str} {mon_str} {current_year} {time_str}", "%d %b %Y %I:%M %p")
    except ValueError:
        return None, current_year, month_num

    start_time = naive.replace(tzinfo=SOURCE_TZ).astimezone(timezone.utc)

    return RawEvent(
        title=title,
        start_time=start_time,
        location=STORE_ADDRESS,
        url=None,
        description=None,
        image_url=_extract_image_url(block),
    ), current_year, month_num


def parse(html: str) -> list[RawEvent]:
    """Parse Black Bird's events-page HTML into RawEvents.

    Browser-free so tests can run against fixture HTML.
    Year is inferred from today in SF-local time and rolled forward if the
    month sequence goes backward (list is chronological).
    """
    soup = BeautifulSoup(html, "html.parser")
    events: list[RawEvent] = []
    year = datetime.now(SOURCE_TZ).year
    prev_month: int | None = None

    for block in soup.select(".ma-event"):
        # Peek month first so a Dec→Jan boundary can bump the year.
        mon_tag = block.select_one(".ma-day__month")
        month_num = _MONTHS.get(mon_tag.get_text(strip=True)[:3]) if mon_tag else None
        if month_num is not None and prev_month is not None and month_num < prev_month:
            year += 1

        event, year, seen_month = _parse_event(block, year)
        if event is not None:
            events.append(event)
        if seen_month is not None:
            prev_month = seen_month

    return events


def scrape(url: str = EVENTS_URL, horizon: date | None = None) -> list[RawEvent]:
    """Fetch Black Bird events, clicking "Load More Events" until we hit the
    look-ahead horizon or the button disappears.
    """
    from playwright.sync_api import TimeoutError as PWTimeoutError, sync_playwright

    if horizon is None:
        horizon = date.today() + timedelta(days=LOOKAHEAD_DAYS)
    horizon_dt = datetime.combine(horizon, datetime.min.time(), tzinfo=timezone.utc)

    seen_urls: set[str] = set()  # unused for Black Bird (no URL) — kept for shape
    seen_keys: set[tuple[str, datetime]] = set()
    all_events: list[RawEvent] = []

    with browser_context() as context:
        page = context.new_page()
        try:
            response = None
            try:
                response = page.goto(url, wait_until=NETWORKIDLE, timeout=45000)
            except PWTimeoutError:
                pass
            if response is not None and response.status in (403, 429):
                print(f"[blackbird] blocked (HTTP {response.status}) at {url}, skipping", flush=True)
                return []
            page.wait_for_timeout(SETTLE_MS)

            for _ in range(LOAD_MORE_MAX_CLICKS + 1):
                # Parse current DOM, collect new events
                html = page.content()
                fresh = 0
                latest_start: datetime | None = None
                for ev in parse(html):
                    key = (ev.title, ev.start_time)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    all_events.append(ev)
                    fresh += 1
                    if latest_start is None or ev.start_time > latest_start:
                        latest_start = ev.start_time

                # Stop conditions
                if latest_start is not None and latest_start > horizon_dt:
                    break
                btn = page.get_by_role("button", name=LOAD_MORE_LABEL)
                if btn.count() == 0 or not btn.first.is_visible():
                    break
                # Click, then wait for new events to append
                prev_count = len(seen_keys)
                btn.first.click()
                page.wait_for_timeout(LOAD_MORE_WAIT_MS)
                # If no new events appeared after a click, bail (button no-op).
                # Do the parse-based check on the next loop iteration; here we
                # just guard against the button becoming unclickable without
                # actually adding events.
                if fresh == 0 and prev_count > 0:
                    break
        finally:
            page.close()

    return all_events
