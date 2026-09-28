"""Partiful — crawl public SF events from partiful.com.

Partiful is mostly invite-link driven, but it publishes a public discovery feed
per region. Everything is server-rendered into Next.js page data
(`<script id="__NEXT_DATA__">`), so plain requests work (Vercel, no WAF):

  1. `/explore/sf` embeds ~60 upcoming public SF events with full data (title,
     start, timezone, structured address, description, image). Scrolling loads
     nothing more — the page *is* the feed.
  2. Each event page (`/e/<id>`) lists `similarEvents` (id + start) for its
     region. We crawl those breadth-first to find events the feed doesn't
     surface, staying in the SF region, skipping ones already started, and
     capped at MAX_EVENT_PAGES fetches with a polite delay.

Only `isPublic` + `PUBLISHED` events are kept by default. `to_raw_event(...,
require_public=False)` exists for links someone sends us directly (consent to
index), for the future email-ingestion path.
"""
from __future__ import annotations

import json
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Callable

import requests

from scrapers.base import RawEvent
from scrapers.bay_area import is_bay_area
from scrapers.browser import BROWSER_UA

SOURCE = "partiful.com"
NAME = "Partiful"
EXPLORE_URL = "https://partiful.com/explore/sf"
EVENT_URL = "https://partiful.com/e/{id}"
IMAGE_URL = "https://partiful.imgix.net/{path}?w=800&fit=clip"
REGION = "SF"
REQUEST_TIMEOUT = 25
# Crawl budget: event-page fetches per run (the explore seeds are fetched too,
# for their similarEvents). ~1 req/s keeps a full crawl a few minutes and
# polite — robots.txt has no rules for us, so keep volume modest.
MAX_EVENT_PAGES = 150
DELAY_S = 1.0
LOG_EVERY = 25
# An event that started a few hours ago may still be on — keep it that long.
STARTED_GRACE = timedelta(hours=6)

_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _log(msg: str) -> None:
    print(f"[partiful] {msg}", flush=True)


def matches(url: str) -> bool:
    return "partiful.com/explore" in url


def page_props(html: str) -> dict:
    """The Next.js `pageProps` embedded in a Partiful page ({} if absent)."""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return {}
    try:
        return json.loads(m.group(1)).get("props", {}).get("pageProps", {}) or {}
    except (ValueError, AttributeError):
        return {}


def parse_explore(html: str) -> list[dict]:
    """Distinct event objects on an explore page, in page order."""
    props = page_props(html)
    sections = [props.get("trendingSection") or {}] + list(props.get("sections") or [])
    items = [i for s in sections for i in (s.get("items") or [])]
    items += list(props.get("feedItems") or [])
    seen, events = set(), []
    for item in items:
        event = item.get("event") if isinstance(item, dict) else None
        if item.get("type", "event") != "event" or not isinstance(event, dict):
            continue
        if event.get("id") and event["id"] not in seen:
            seen.add(event["id"])
            events.append(event)
    return events


def parse_event_page(html: str) -> tuple[dict | None, list[dict], str | None]:
    """(event, similarEvents, similarEventsRegion) from an event page."""
    props = page_props(html)
    event = props.get("event") if isinstance(props.get("event"), dict) else None
    return event, list(props.get("similarEvents") or []), props.get("similarEventsRegion")


def _parse_start(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        start = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return start.astimezone(timezone.utc) if start.tzinfo else None


def _location(event: dict) -> str | None:
    """"<place>, <address lines>" for structured locations; None otherwise.

    Freeform locations are notes ("text me for the address"), not places.
    """
    info = event.get("locationInfo") or {}
    if info.get("type") != "structured":
        return None
    maps = info.get("mapsInfo") or {}
    address = ", ".join(line.strip() for line in (maps.get("addressLines") or []) if line and line.strip())
    name = (maps.get("name") or "").strip()
    parts = ([name] if name and name not in address else []) + ([address] if address else [])
    return ", ".join(parts) or maps.get("approximateLocation") or event.get("location") or None


def _image(event: dict) -> str | None:
    image = event.get("image") or {}
    path = (image.get("upload") or {}).get("path")
    return IMAGE_URL.format(path=path) if path else image.get("url")


def to_raw_event(event: dict, *, now: datetime | None = None,
                 require_public: bool = True) -> RawEvent | None:
    """Map a Partiful event object to a RawEvent, or None if it's filtered out
    (not public, not published, already over, or located outside the Bay Area).
    """
    now = now or datetime.now(timezone.utc)
    title = (event.get("title") or "").strip()
    start = _parse_start(event.get("startDate"))
    if not title or not event.get("id") or start is None:
        return None
    if require_public and not event.get("isPublic"):
        return None
    if event.get("status", "PUBLISHED") != "PUBLISHED":
        return None
    if start < now - STARTED_GRACE:
        return None
    location = _location(event)
    if location and not is_bay_area(location):
        return None
    return RawEvent(
        title=title,
        start_time=start,
        location=location,
        url=EVENT_URL.format(id=event["id"]),
        description=(event.get("description") or "").strip() or None,
        image_url=_image(event),
    )


def crawl(seeds: list[dict], fetch: Callable[[str], str], *, max_pages: int = MAX_EVENT_PAGES,
          now: datetime | None = None, delay_s: float = DELAY_S) -> list[RawEvent]:
    """Seeds (explore events) → events, plus a breadth-first walk of each event
    page's similarEvents within the SF region, up to `max_pages` fetches.

    Seeds are trusted as SF (they come from /explore/sf). A crawled event is kept
    only if its own page reports the SF region. A failing page is logged and
    skipped; the crawl continues.
    """
    now = now or datetime.now(timezone.utc)
    events: dict[str, RawEvent] = {}
    for seed in seeds:
        raw = to_raw_event(seed, now=now)
        if raw:
            events[seed["id"]] = raw

    queue = deque(seed["id"] for seed in seeds if seed.get("id"))
    queued = set(queue)
    fetched = failed = 0
    while queue and fetched < max_pages:
        event_id = queue.popleft()
        url = EVENT_URL.format(id=event_id)
        if fetched and delay_s:
            time.sleep(delay_s)
        fetched += 1
        try:
            event, similar, region = parse_event_page(fetch(url))
        except Exception as e:  # one bad page shouldn't sink the crawl
            failed += 1
            _log(f"event page failed {url}: {type(e).__name__}: {e}")
            continue
        if region != REGION:
            continue
        if event and event_id not in events:
            raw = to_raw_event(event, now=now)
            if raw:
                events[event_id] = raw
        for s in similar:
            sid, start = s.get("id"), _parse_start(s.get("startDate"))
            if sid and sid not in queued and start and start >= now - STARTED_GRACE:
                queued.add(sid)
                queue.append(sid)
        if fetched % LOG_EVERY == 0:
            _log(f"crawled {fetched} pages, {len(events)} events, {len(queue)} queued")
    _log(f"done: {len(events)} events from {len(seeds)} seeds + {fetched} pages "
         f"({failed} failed, {len(queue)} left unvisited)")
    return list(events.values())


def _fetch(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def scrape(url: str = EXPLORE_URL) -> list[RawEvent]:
    seeds = parse_explore(_fetch(url))
    _log(f"explore: {len(seeds)} events")
    return crawl(seeds, _fetch)
