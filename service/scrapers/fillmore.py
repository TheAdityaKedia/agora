"""The Fillmore (San Francisco) events scraper.

Live Nation embeds one <script type="application/ld+json"> per show, each a
schema.org MusicEvent with everything we need: name, startDate (ISO 8601 with
timezone!), url (Ticketmaster event page), image, and location.name.

Because the page is a big Chakra UI SPA where the readable HTML is thin, the
JSON-LD blocks are actually the cleanest data source. No DOM parsing needed.
"""
import json
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "thefillmore.com"
EVENTS_URL = "https://www.thefillmore.com/shows"

VENUE = "The Fillmore, 1805 Geary Blvd, San Francisco, CA 94115"
REQUEST_TIMEOUT = 25


def matches(url: str) -> bool:
    return "thefillmore.com" in url


def _iter_music_events(html: str):
    """Yield decoded JSON-LD objects with @type MusicEvent."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for obj in _flatten(payload):
            if isinstance(obj, dict) and obj.get("@type") == "MusicEvent":
                yield obj


def _flatten(payload):
    """Yield dicts from a JSON-LD payload that may be a dict, list, or @graph."""
    if isinstance(payload, list):
        for item in payload:
            yield from _flatten(item)
    elif isinstance(payload, dict):
        graph = payload.get("@graph")
        if isinstance(graph, list):
            yield from _flatten(graph)
        else:
            yield payload


def _location_name(event: dict) -> str:
    loc = event.get("location")
    if isinstance(loc, dict):
        name = loc.get("name")
        if name:
            # Fall back to VENUE if the source only gives a bare name; the full
            # address is more useful in a mixed-source calendar.
            return VENUE if name == "The Fillmore" else name
    return VENUE


def parse(html: str) -> list[RawEvent]:
    events: list[RawEvent] = []
    for obj in _iter_music_events(html):
        name = obj.get("name")
        start = obj.get("startDate")
        if not (name and start):
            continue
        try:
            # ISO 8601 with tz offset ("2026-09-21T20:00:00-07:00"). Normalize
            # to UTC to match the rest of the pipeline.
            start_time = datetime.fromisoformat(start).astimezone(timezone.utc)
        except ValueError:
            continue
        image = obj.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        events.append(RawEvent(
            title=name,
            start_time=start_time,
            location=_location_name(obj),
            url=obj.get("url"),
            description=obj.get("description"),
            image_url=image if isinstance(image, str) else None,
        ))
    return events


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[fillmore] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    return parse(resp.text)
