import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from config import LOOKAHEAD_DAYS


BASE_URL = "https://greenapplebooks.com"
# Green Apple lists times in local (San Francisco) time with no tz marker.
SOURCE_TZ = ZoneInfo("America/Los_Angeles")

# Green Apple is behind Cloudflare's managed-challenge bot protection, so a plain
# requests.get() gets a 403. We drive a headless browser to solve the JS challenge,
# then hand the rendered HTML to the same BeautifulSoup parser.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# Max time to wait for event rows to render (Cloudflare challenge + page load).
PAGE_READY_TIMEOUT_MS = 20000
# Politeness delay between page fetches within a single scrape run. Green Apple
# sits behind Cloudflare, and rapid-fire pagination triggers a 429. This keeps
# us well under the threshold.
BETWEEN_PAGE_DELAY_MS = 2500
# Safety cap so pagination can't run away if a "Next Month" link ever loops.
MAX_PAGES = 24


@dataclass
class RawEvent:
    title: str
    start_time: datetime
    location: str | None
    url: str
    description: str | None


def _parse_datetime(date_str: str, time_str: str) -> datetime | None:
    # date_str: "Mon, 5/4/2026"  time_str: "7:00pm"
    try:
        date_part = date_str.split(", ", 1)[-1].strip()  # "5/4/2026"
        naive = datetime.strptime(f"{date_part} {time_str.strip()}", "%m/%d/%Y %I:%M%p")
        # Interpret as San Francisco local time, store canonical UTC.
        return naive.replace(tzinfo=SOURCE_TZ).astimezone(timezone.utc)
    except ValueError:
        return None


def _extract_detail(row, label: str) -> str | None:
    for item in row.select("div.event-list__details--item"):
        lbl = item.find("span", class_="event-list__details--label")
        if lbl and label in lbl.get_text():
            # remove the label span then return remaining text
            lbl.extract()
            return item.get_text(separator=" ", strip=True)
    return None


def _load_page_html(context, url: str) -> str:
    """Load `url` in a fresh page and return its HTML once event rows render.

    Waits for `div.views-row` — which is present on both event-bearing pages
    (real event rows) and empty months (a single placeholder container).
    On timeout we still return whatever loaded; parse() yields no events
    for a page it can't interpret, and pagination continues on the next month.
    """
    from playwright.sync_api import TimeoutError as PWTimeoutError

    page = context.new_page()
    try:
        # `load` waits for the full onload event (all resources, not just DOM).
        # More reliable than `domcontentloaded` for Drupal pages where
        # Cloudflare + late-rendering rows/pager cause reads of a stale DOM.
        response = None
        try:
            response = page.goto(url, wait_until="load", timeout=PAGE_READY_TIMEOUT_MS)
        except PWTimeoutError:
            pass
        if response is not None and response.status in (403, 429):
            # 429 = rate-limited outright; 403 with a "Just a moment..." title
            # is Cloudflare's re-challenge, which requests can't solve without
            # a full re-render cycle. Both mean: back off, stop gracefully.
            raise RateLimited(url, response.status)
        return page.content()
    finally:
        page.close()


class RateLimited(Exception):
    """Raised when the source blocks us (429 or Cloudflare 403 challenge)."""
    def __init__(self, url: str, status: int):
        super().__init__(f"{status} for {url}")
        self.url = url
        self.status = status


def fetch(url: str) -> str:
    """Return the fully rendered HTML for a single `url` (one-shot browser session)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent=BROWSER_UA,
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            return _load_page_html(context, url)
        finally:
            browser.close()


def parse(html: str) -> list[RawEvent]:
    """Parse Green Apple's events-page HTML into RawEvents.

    Pure and browser-free so it can be unit-tested against fixture HTML.
    """
    soup = BeautifulSoup(html, "html.parser")

    events = []
    for row in soup.select("div.views-row"):
        title_tag = row.find("h3", class_="event-list__title")
        if not title_tag:
            continue

        link = title_tag.find("a")
        if not link:
            continue

        title = link.get_text(strip=True)
        event_url = urljoin(BASE_URL, link["href"])

        date_str = _extract_detail(row, "Date:")
        time_str = _extract_detail(row, "Time:")

        description_tag = row.find("div", class_="event-list__body")
        description = description_tag.get_text(strip=True) if description_tag else None

        location_tag = row.find("address")
        location = location_tag.get_text(separator=", ", strip=True) if location_tag else None

        if not date_str or not time_str:
            continue

        start_time = _parse_datetime(date_str, time_str)
        if not start_time:
            continue

        events.append(RawEvent(
            title=title,
            start_time=start_time,
            location=location,
            url=event_url,
            description=description,
        ))

    return events


def find_next_month_url(html: str) -> str | None:
    """Return the absolute URL of the 'Next Month' link on the page, or None."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        if a.get_text(strip=True) == "Next Month":
            return urljoin(BASE_URL, a["href"])
    return None


_MONTH_URL_RE = re.compile(r"/events/(\d{4})/(\d{1,2})/?$")


def _first_of_month_from_url(url: str) -> date | None:
    """Parse a Green Apple `/events/YYYY/MM` URL to the first day of that month."""
    m = _MONTH_URL_RE.search(url)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), 1)
    except ValueError:
        return None


def scrape(url: str, horizon: date | None = None) -> list[RawEvent]:
    """Walk Green Apple's month-paginated event listing from `url` forward.

    Follows the site's "Next Month" link, stopping when:
      - the next-month URL is past `horizon` (defaults to today + LOOKAHEAD_DAYS)
      - there is no next-month link
      - we would revisit a page we've already seen (loop guard)
      - MAX_PAGES safety cap

    Deduplicates within-run by event URL so an event that appears on two
    adjacent months is counted once.
    """
    from playwright.sync_api import sync_playwright

    if horizon is None:
        horizon = date.today() + timedelta(days=LOOKAHEAD_DAYS)

    all_events: list[RawEvent] = []
    seen_urls: set[str] = set()
    visited_pages: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent=BROWSER_UA,
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            current = url
            for i in range(MAX_PAGES):
                if current in visited_pages:
                    break
                visited_pages.add(current)
                try:
                    html = _load_page_html(context, current)
                except RateLimited as e:
                    print(f"[greenapple] blocked (HTTP {e.status}) at {e.url}, stopping early", flush=True)
                    break
                for ev in parse(html):
                    if ev.url in seen_urls:
                        continue
                    seen_urls.add(ev.url)
                    all_events.append(ev)
                nxt = find_next_month_url(html)
                if not nxt:
                    break
                # Green Apple offers "Next Month" links into perpetuity, so the
                # horizon check is the real end condition.
                nxt_month = _first_of_month_from_url(nxt)
                if nxt_month and nxt_month > horizon:
                    break
                # Politeness delay to stay well under Cloudflare's rate limit.
                time.sleep(BETWEEN_PAGE_DELAY_MS / 1000.0)
                current = nxt
        finally:
            browser.close()

    return all_events
