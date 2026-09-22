"""Shared Luma (luma.com) calendar scraping helpers.

Any organizer that publishes its calendar via a Luma page (e.g.
https://luma.com/Big-Brain-Bay) can plug in with a thin per-source wrapper —
see scrapers/bigbrainbay.py for an example. This module handles:

  1. Calendar-page fetch (plain requests — server-rendered HTML).
  2. Extracting each event's schema.org Event object from the calendar page's
     embedded JSON-LD `ItemList`.
  3. Mapping Event → RawEvent (title, start_time, location, url, image).
  4. Fetching each event's detail page for the description (Luma omits
     descriptions from the calendar-level payload).
  5. Concurrent detail-page fetches — Luma is not WAF-guarded, so a
     ThreadPoolExecutor(5) gets an order-of-magnitude speedup.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable, Optional

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


BASE_URL = "https://luma.com"
REQUEST_TIMEOUT = 25
# Luma is fast and not rate-limited; parallel fetches shave ~5x off Phase 2.
DETAIL_WORKERS = 5
# Emit progress every N detail fetches so long runs don't feel stuck.
DETAIL_LOG_EVERY = 20


def _log(msg: str) -> None:
    print(f"[luma] {msg}", flush=True)


def _iter_json_ld(html: str):
    """Yield every parsed JSON-LD payload from a page. Silently skips
    malformed blocks so a single garbled preview doesn't abort a scrape.
    """
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue


def find_calendar_events(html: str) -> list[dict]:
    """Return the list of embedded Event dicts from a Luma calendar page.

    Luma emits one `ItemList` JSON-LD block with every upcoming event as a
    ListItem whose `item` is the Event.
    """
    for obj in _iter_json_ld(html):
        if isinstance(obj, dict) and obj.get("@type") == "ItemList":
            events: list[dict] = []
            for it in obj.get("itemListElement") or []:
                if isinstance(it, dict):
                    item = it.get("item")
                    if isinstance(item, dict) and item.get("@type") == "Event":
                        events.append(item)
            return events
    return []


def _format_location(loc) -> Optional[str]:
    """Build a human-readable location from schema.org Place.

    Priority: venue name → street → "City, Region". Deduplicates a common
    Luma pattern where the same string appears as both `name` and
    `streetAddress` (e.g. "Donkey & Goat Winery" in both fields).
    """
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
        if street and street != name:
            parts.append(street)
        if city and region:
            parts.append(f"{city}, {region}")
        elif city:
            parts.append(city)
    return ", ".join(parts) if parts else name


def event_from_json_ld(obj: dict) -> Optional[RawEvent]:
    """Convert a schema.org Event dict to a RawEvent, or None if required
    fields (name, startDate) are missing or malformed.

    The calendar-level payload omits `description` — callers enrich it from
    the detail page separately.
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
    return RawEvent(
        title=name,
        start_time=start_time,
        location=_format_location(obj.get("location")),
        url=obj.get("url") or obj.get("@id"),
        description=obj.get("description"),
        image_url=image if isinstance(image, str) else None,
    )


def parse_event_description(html: str) -> Optional[str]:
    """Return the event's description from a detail page.

    Prefers the JSON-LD `Event.description` (which is the full, unabridged
    text); falls back to `<meta property="og:description">` (truncated at
    ~300 chars).
    """
    for obj in _iter_json_ld(html):
        if isinstance(obj, dict) and obj.get("@type") == "Event":
            desc = obj.get("description")
            if isinstance(desc, str) and desc.strip():
                return desc.strip()
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", attrs={"property": "og:description"})
    if og and og.get("content"):
        text = og["content"].strip()
        if text:
            return text
    return None


def fetch_calendar_html(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def fetch_event_html(url: str) -> Optional[str]:
    """Fetch a detail page and return its HTML, or None on any error."""
    try:
        resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None
        return resp.text
    except requests.RequestException:
        return None


def _fetch_and_parse(fetch_fn: Callable[[str], Optional[str]], url: str) -> Optional[str]:
    html = fetch_fn(url)
    return parse_event_description(html) if html else None


def scrape_calendar(
    calendar_url: str,
    *,
    calendar_html_fetch: Optional[Callable[[str], str]] = None,
    event_html_fetch: Optional[Callable[[str], Optional[str]]] = None,
) -> list[RawEvent]:
    """Fetch the Luma calendar page and return one RawEvent per event,
    enriched with a description from each event's detail page.

    `calendar_html_fetch` and `event_html_fetch` let tests inject stubbed
    responses; both default to the built-in HTTP fetchers.
    """
    calendar_html_fetch = calendar_html_fetch or fetch_calendar_html
    event_html_fetch = event_html_fetch or fetch_event_html

    _log(f"phase 1: fetching {calendar_url}")
    try:
        html = calendar_html_fetch(calendar_url)
    except requests.RequestException as e:
        _log(f"calendar fetch failed: {type(e).__name__}: {e}")
        return []

    raw_events = find_calendar_events(html)
    _log(f"calendar has {len(raw_events)} events")
    events: list[RawEvent] = []
    for obj in raw_events:
        ev = event_from_json_ld(obj)
        if ev is not None:
            events.append(ev)
    if not events:
        return events

    unique_urls = list(dict.fromkeys(ev.url for ev in events if ev.url))
    _log(f"phase 2: fetching descriptions for {len(unique_urls)} detail pages "
         f"({DETAIL_WORKERS} workers)")
    descriptions: dict[str, str] = {}
    t_phase = time.monotonic()
    completed = 0
    total = len(unique_urls)
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        futures = {pool.submit(_fetch_and_parse, event_html_fetch, u): u for u in unique_urls}
        for fut in as_completed(futures):
            completed += 1
            url = futures[fut]
            desc = fut.result()
            if desc:
                descriptions[url] = desc
            if completed % DETAIL_LOG_EVERY == 0 or completed == total:
                _log(f"detail {completed}/{total} fetched "
                     f"({len(descriptions)} with descriptions, "
                     f"{time.monotonic() - t_phase:.0f}s elapsed)")

    enriched = 0
    for ev in events:
        if ev.url and ev.url in descriptions:
            ev.description = descriptions[ev.url]
            enriched += 1
    _log(f"done: {len(events)} events, {enriched} with rich descriptions")
    return events
