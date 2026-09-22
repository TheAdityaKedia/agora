"""Black Bird Bookstore events scraper.

Black Bird's events page is a Shopify site using the Mahina Events app. The
widget is backed by a JSON API — a POST to the shop's Mahina endpoint returns
fully structured events (id, title, HTML description, image, ISO startDate),
paginated 12 at a time. We hit that API directly, so no headless browser is
needed and we get real descriptions, precise UTC start times, and a per-event
URL (the app's `#?event-id=<id>` deep link) for free.
"""
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "blackbirdsf.com"
NAME = "Black Bird Bookstore"
SHOP = "black-bird-bookstore.myshopify.com"
API_URL = f"https://mahina.app/app/{SHOP}"
EVENTS_URL = "https://blackbirdsf.com/pages/events"
STORE_ADDRESS = "Black Bird Bookstore, 4033 Judah St, San Francisco, CA 94122"
REQUEST_TIMEOUT = 25
PAGE_SIZE = 12  # events per API page; a short page means we've reached the end
MAX_PAGES = 50  # safety bound


def matches(url: str) -> bool:
    return "blackbirdsf.com" in url


def _html_to_text(html: str | None) -> str | None:
    """Flatten the Mahina rich-text (HTML) description to plain text."""
    if not html:
        return None
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return text or None


def _event_url(event_id) -> str | None:
    """The Mahina widget's deep link for a single event, or None."""
    return f"{EVENTS_URL}#?event-id={event_id}" if event_id is not None else None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_events(data: dict) -> list[RawEvent]:
    """Parse one Mahina API page (a dict with an 'events' list) into RawEvents.

    Pure — no network — so it's testable against a captured API response.
    """
    events: list[RawEvent] = []
    for e in data.get("events") or []:
        title = e.get("title")
        start_time = _parse_iso(e.get("startDate"))
        if not (title and start_time):
            continue
        location = (e.get("location") or {}).get("name") or STORE_ADDRESS
        events.append(RawEvent(
            title=title,
            start_time=start_time.astimezone(timezone.utc),
            location=location,
            url=_event_url(e.get("id")),
            description=_html_to_text(e.get("description")),
            image_url=e.get("image") or None,
        ))
    return events


def _fetch_page(page: int) -> dict:
    resp = requests.post(
        API_URL,
        json={"shop": SHOP, "selectedEventId": None, "selectedRecurringDate": None, "page": page},
        headers={"User-Agent": BROWSER_UA, "Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    """Fetch all Black Bird events from the Mahina API, paginating to the end."""
    events: list[RawEvent] = []
    for page in range(1, MAX_PAGES + 1):
        try:
            data = _fetch_page(page)
        except (requests.RequestException, ValueError) as e:
            print(f"[blackbird] API fetch failed on page {page}: {e}", flush=True)
            break
        events.extend(parse_events(data))
        if len(data.get("events") or []) < PAGE_SIZE:
            break
    return events
