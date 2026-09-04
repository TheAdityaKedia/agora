from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


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
# Seconds to let Cloudflare's challenge JS run before reading page content.
CHALLENGE_WAIT_MS = 6000


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


def fetch(url: str) -> str:
    """Return the fully rendered HTML for `url`, clearing Cloudflare's challenge.

    Uses a headless Chromium via Playwright. Imported lazily so unit tests that
    parse fixture HTML don't require the browser to be installed.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent=BROWSER_UA,
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(CHALLENGE_WAIT_MS)
            return page.content()
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


def scrape(url: str) -> list[RawEvent]:
    return parse(fetch(url))
