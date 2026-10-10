"""Shared helpers for Live Nation venue sites (single-venue "/shows" pages).

Live Nation's venue sites (The Masonic, Cobb's, Punch Line, The Fillmore…) are
one Chakra UI SPA template that embeds one ``<script type="application/ld+json">``
per show — a schema.org ``MusicEvent`` (even for comedy) with name, a
tz-offset ``startDate``, a Ticketmaster ``url``, an image and ``location.name``.
The server-rendered ``/shows`` page carries the next ~36 shows; the rest sit
behind a client-side "load more", so the daily scrape keeps a rolling window.

Every show on these sites is at the one venue, so the location is the venue's
full address (``location.name`` is only a bare name). A new venue is a thin
wrapper supplying its ``/shows`` URL and address (see scrapers/sfmasonic.py).
scrapers/fillmore.py predates this module and keeps its own copy.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 25
_EVENT_TYPES = {"MusicEvent", "Event", "ComedyEvent", "TheaterEvent"}


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


def _iter_events(html: str):
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        for obj in _flatten(payload):
            if isinstance(obj, dict) and obj.get("@type") in _EVENT_TYPES:
                yield obj


def parse(html: str, *, venue: str) -> list[RawEvent]:
    """JSON-LD events on a Live Nation venue page → RawEvents. Pure."""
    events: list[RawEvent] = []
    seen: set[tuple[str, datetime]] = set()
    for obj in _iter_events(html):
        name, start = obj.get("name"), obj.get("startDate")
        if not (name and start):
            continue
        # schema.org EventCancelled / EventPostponed: kept, with the status.
        state = obj.get("eventStatus") or ""
        status = "cancelled" if "Cancelled" in state else "postponed" if "Postponed" in state else None
        try:
            start_time = datetime.fromisoformat(start)
        except ValueError:
            continue
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=SOURCE_TZ)
        start_time = start_time.astimezone(timezone.utc)
        if (name, start_time) in seen:
            continue
        seen.add((name, start_time))
        image = obj.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        events.append(RawEvent(
            title=name.strip(),
            start_time=start_time,
            location=venue,
            url=obj.get("url"),
            description=obj.get("description"),
            image_url=image if isinstance(image, str) else None,
            status=status,
        ))
    return events


def scrape_shows(url: str, *, venue: str, tag: str = "livenation") -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[{tag}] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    events = parse(resp.text, venue=venue)
    print(f"[{tag}] {len(events)} shows", flush=True)
    return events
