"""Shared Eventbrite scraping helpers (library, not a scraper itself).

Any venue that publishes its calendar via an Eventbrite organizer page can
plug in with a thin per-source wrapper — see scrapers/phoenix.py for an
example. This module handles:

  1. Organizer-page fetch (Playwright — the listing is JS-rendered).
  2. Extracting event URLs from that page.
  3. Fetching each event's detail page (plain requests — server-rendered).
  4. Parsing schema.org Event JSON-LD from the detail page.
  5. Politeness delays between event fetches.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import RateLimited, browser_context, load_page_html


BASE_URL = "https://www.eventbrite.com"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ORGANIZER_WAIT_UNTIL = "load"
ORGANIZER_SETTLE_MS = 4000
REQUEST_TIMEOUT = 25
BETWEEN_EVENT_DELAY_S = 0.7


def find_organizer_event_urls(html: str) -> list[str]:
    """Return absolute event URLs from an organizer page's rendered HTML.

    Eventbrite formats each event card's anchor as `<a href="/e/<slug>-tickets-<id>">`.
    Deduped in first-seen order.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0]
        if "/e/" not in href or "tickets-" not in href:
            continue
        abs_url = urljoin(BASE_URL, href).rstrip("/")
        if abs_url in seen:
            continue
        seen.add(abs_url)
        urls.append(abs_url)
    return urls


def parse_event_page(html: str) -> dict | None:
    """Return the decoded schema.org Event JSON-LD object, or None.

    Eventbrite embeds one <script type="application/ld+json"> per @type
    on each event page. The interesting one has @type == 'Event'.
    """
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for candidate in _flatten(obj):
            t = candidate.get("@type")
            if t == "Event" or (isinstance(t, list) and "Event" in t):
                return candidate
    return None


def _flatten(payload):
    if isinstance(payload, list):
        for item in payload:
            yield from _flatten(item)
    elif isinstance(payload, dict):
        graph = payload.get("@graph")
        if isinstance(graph, list):
            yield from _flatten(graph)
        else:
            yield payload


def event_from_json_ld(obj: dict, *, location_override: str | None = None) -> RawEvent | None:
    """Convert a schema.org Event JSON-LD dict to a RawEvent, or None if
    required fields are missing.
    """
    name = obj.get("name")
    start = obj.get("startDate")
    if not (name and start):
        return None
    try:
        start_time = datetime.fromisoformat(start).astimezone(timezone.utc)
    except ValueError:
        return None

    image = obj.get("image")
    if isinstance(image, list):
        image = image[0] if image else None

    location = location_override or _format_location(obj.get("location"))

    return RawEvent(
        title=name,
        start_time=start_time,
        location=location,
        url=obj.get("url"),
        description=obj.get("description"),
        image_url=image if isinstance(image, str) else None,
    )


def _format_location(loc) -> str | None:
    if not isinstance(loc, dict):
        return None
    name = loc.get("name")
    addr = loc.get("address")
    parts: list[str] = []
    if name:
        parts.append(name)
    if isinstance(addr, dict):
        street = addr.get("streetAddress")
        city = addr.get("addressLocality")
        region = addr.get("addressRegion")
        if street:
            parts.append(street)
        if city and region:
            parts.append(f"{city}, {region}")
        elif city:
            parts.append(city)
    return ", ".join(parts) if parts else name


def fetch_organizer_html(url: str) -> str:
    """Render an organizer page via Playwright so its event tiles are present."""
    with browser_context() as context:
        return load_page_html(context, url, wait_until=ORGANIZER_WAIT_UNTIL, settle_ms=ORGANIZER_SETTLE_MS)


def fetch_event_html(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def scrape_organizer(
    organizer_url: str,
    *,
    location_override: str | None = None,
    max_events: int = 60,
    organizer_html_fetch=None,
    event_html_fetch=None,
) -> list[RawEvent]:
    """Walk an Eventbrite organizer page and return one RawEvent per event.

    `organizer_html_fetch(url) -> html` and `event_html_fetch(url) -> html` let
    tests inject stubbed responses; both default to the built-in Playwright /
    requests fetchers.
    """
    if organizer_html_fetch is None:
        organizer_html_fetch = fetch_organizer_html
    if event_html_fetch is None:
        event_html_fetch = fetch_event_html

    try:
        organizer_html = organizer_html_fetch(organizer_url)
    except RateLimited as e:
        print(f"[eventbrite] blocked (HTTP {e.status}) at {e.url}, skipping", flush=True)
        return []

    event_urls = find_organizer_event_urls(organizer_html)[:max_events]
    events: list[RawEvent] = []
    for i, url in enumerate(event_urls):
        try:
            html = event_html_fetch(url)
        except Exception as e:
            print(f"[eventbrite] detail fetch failed for {url}: {e}", flush=True)
            continue
        obj = parse_event_page(html)
        if obj is None:
            continue
        ev = event_from_json_ld(obj, location_override=location_override)
        if ev is not None:
            events.append(ev)
        if i < len(event_urls) - 1:
            time.sleep(BETWEEN_EVENT_DELAY_S)
    return events
