"""Great American Music Hall events scraper.

GAMH publishes its calendar via a SeeTickets widget. Each event lives in
`.seetickets-list-event-container` with:
  - .event-title a[href] → title + Ticketing URL (absolute)
  - .seetickets-list-view-event-image[src] → poster
  - .event-date ("Sun Sep 20") — day + short month, no year
  - .see-showtime ("8:00PM") — the actual start time
  - .event-header, .supporting-talent, .genre — extra description context

Year is inferred from the current SF-local year, rolling forward when the
month sequence goes backward across the list (same approach as Black Bird).

Pagination: the calendar page server-renders only the first 12 events; the rest
(~6 more pages) load via the plugin's "Load more events" button, which GETs
`admin-ajax.php?action=get_seetickets_events&seeAjaxPage=N&nonce=...` and
returns more card fragments. The nonce is embedded in the page
(`seetickets_ajax_obj`); the page count is on the button
(`data-see-total-pages`). All pages are joined and parsed in ONE pass, so the
year rollover sees the whole Sep -> Feb sequence; parsing pages separately
would restart each at the current year and date January shows a year early.

robots.txt note: GAMH's (Flywheel's default) robots.txt has `Disallow: /*?`,
which on its face covers this query-string endpoint. We asked GAMH; they're OK
with us using it (it's the same request their own page makes for every visitor
who clicks "load more"). We do honor its `Crawl-delay: 3` between requests.
Don't "fix" this back to page-1-only on robots grounds.

Descriptions stay short (header + support acts + genre): the Eventim event
pages that carry the full text sit behind a Cloudflare Turnstile CAPTCHA, and
we don't try to get past that.
"""
import re
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "gamh.com"
NAME = "Great American Music Hall"
EVENTS_URL = "https://gamh.com/calendar/"
AJAX_URL = "https://gamh.com/wp-admin/admin-ajax.php"
# robots.txt `Crawl-delay: 3`, honored between every request.
CRAWL_DELAY_S = 3
# Safety cap in case data-see-total-pages is ever absurd (it's ~7 today).
MAX_PAGES = 20
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "Great American Music Hall, 859 O'Farrell St, San Francisco, CA 94109"
REQUEST_TIMEOUT = 25

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1
)}
# "Sun Sep 20"
_DATE_RE = re.compile(r"^[A-Za-z]{3}\s+([A-Za-z]{3})\s+(\d{1,2})$")
# "8:00PM" or "8:00 PM"
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*([APap][Mm])$")
_NONCE_RE = re.compile(r'seetickets_ajax_obj\s*=\s*\{[^}]*"nonce"\s*:\s*"([^"]+)"')


def matches(url: str) -> bool:
    return "gamh.com" in url


def _parse_month_day(text: str) -> tuple[int, int] | None:
    text = " ".join(text.strip().split())
    m = _DATE_RE.match(text)
    if not m:
        return None
    month = _MONTHS.get(m.group(1)[:3].title())
    if month is None:
        return None
    try:
        return month, int(m.group(2))
    except ValueError:
        return None


def _parse_time(text: str) -> tuple[int, int] | None:
    text = " ".join(text.strip().split())
    m = _TIME_RE.match(text)
    if not m:
        return None
    hour = int(m.group(1)) % 12
    if m.group(3).lower() == "pm":
        hour += 12
    return hour, int(m.group(2))


def _parse_card(card, year: int) -> tuple[RawEvent | None, int | None]:
    title_a = card.select_one(".event-title a")
    if not title_a:
        return None, None
    title = title_a.get_text(strip=True)
    href = title_a.get("href")

    date_tag = card.select_one(".event-date")
    time_tag = card.select_one(".see-showtime")
    if not (date_tag and time_tag):
        return None, None
    md = _parse_month_day(date_tag.get_text(strip=True))
    hm = _parse_time(time_tag.get_text(strip=True))
    if not (md and hm):
        return None, None
    month, day = md
    hour, minute = hm

    try:
        start_time = datetime(year, month, day, hour, minute, tzinfo=SOURCE_TZ).astimezone(timezone.utc)
    except ValueError:
        return None, month

    img_tag = card.select_one(".seetickets-list-view-event-image")
    image_url = img_tag.get("src") if img_tag and img_tag.get("src") else None

    bits = []
    for sel in (".event-header", ".supporting-talent", ".genre"):
        el = card.select_one(sel)
        if el:
            txt = el.get_text(" ", strip=True)
            if txt:
                bits.append(txt)
    description = " · ".join(bits) if bits else None

    return RawEvent(
        title=title,
        start_time=start_time,
        location=VENUE,
        url=href if href else None,
        description=description,
        image_url=image_url,
    ), month


def parse(html: str) -> list[RawEvent]:
    soup = BeautifulSoup(html, "html.parser")
    year = datetime.now(SOURCE_TZ).year
    prev_month: int | None = None
    events: list[RawEvent] = []
    for card in soup.select(".seetickets-list-event-container"):
        # Peek month for year rollover
        date_tag = card.select_one(".event-date")
        peek = _parse_month_day(date_tag.get_text(strip=True)) if date_tag else None
        if peek and prev_month is not None and peek[0] < prev_month:
            year += 1
        ev, month = _parse_card(card, year)
        if ev is not None:
            events.append(ev)
        if month is not None:
            prev_month = month
    return events


def find_pagination(html: str) -> tuple[str | None, int, str]:
    """Return (nonce, total_pages, list_type) for the "Load more" button. Pure.

    No nonce or no button means there's nothing more to load: (None, 1, ...).
    """
    m = _NONCE_RE.search(html)
    btn = BeautifulSoup(html, "html.parser").select_one(".seetickets-load-more-btn")
    if not (m and btn):
        return None, 1, "grid"
    try:
        total = int(btn.get("data-see-total-pages") or 1)
    except ValueError:
        total = 1
    return m.group(1), total, btn.get("data-list-type") or "grid"


def _log(msg: str) -> None:
    print(f"[gamh] {msg}", flush=True)


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    """Fetch the calendar page plus every "load more" page, then parse once."""
    session = requests.Session()
    session.headers["User-Agent"] = BROWSER_UA
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        _log(f"blocked (HTTP {resp.status_code}) at {url}, skipping")
        return []
    resp.raise_for_status()

    pages = [resp.text]
    nonce, total, list_type = find_pagination(resp.text)
    for page in range(2, min(total, MAX_PAGES) + 1):
        time.sleep(CRAWL_DELAY_S)
        try:
            r = session.get(
                AJAX_URL, timeout=REQUEST_TIMEOUT,
                params={"action": "get_seetickets_events", "seeAjaxPage": page,
                        "listType": list_type, "nonce": nonce},
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": url},
            )
        except requests.RequestException as e:
            _log(f"load-more page {page} failed ({type(e).__name__}); keeping what we have")
            break
        if r.status_code != 200:
            _log(f"load-more page {page}: HTTP {r.status_code}; keeping what we have")
            break
        if "seetickets-list-event-container" not in r.text:
            break  # past the last page
        pages.append(r.text)

    events = parse("\n".join(pages))
    _log(f"done: {len(events)} events from {len(pages)} of {total} pages")
    return events
