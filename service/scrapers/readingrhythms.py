"""Reading Rhythms — reading parties, from readingrhythms.co.

Reading Rhythms ("read with friends to live music and curated playlists")
moved its listings off Luma (the old luma.com/readingrhythms-ca calendar
404s since 2026-09) to its own site. `/events` is one server-rendered page
linking every upcoming event across all cities as
`/events/<city-slug>/<event-slug>`; per-city pages just redirect to anchors on
it. Each event page carries a schema.org Event in JSON-LD (start/end in UTC,
venue + street address, image, description). Tickets still sell through a
per-event Luma link — we link the info page instead.

Only Bay Area cities are fetched (the slug names the city), and the event's
own address is checked again (scrapers/bay_area.py).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.bay_area import is_bay_area
from scrapers.browser import BROWSER_UA

SOURCE = "readingrhythms.co"
NAME = "Reading Rhythms"  # unchanged from the Luma era, so existing rows keep their source
SITE = "https://readingrhythms.co"
EVENTS_URL = f"{SITE}/events"
REQUEST_TIMEOUT = 25

_EVENT_PATH_RE = re.compile(r"^/events/([a-z0-9-]+)/[a-z0-9-]+/?$")
# Markdown emphasis/headings in descriptions, and zero-width characters.
_MARKDOWN_RE = re.compile(r"(^|\n)\s*#{1,6}\s*|\*{1,3}|_{2,}")
_INVISIBLE_RE = re.compile(r"[​‌‍﻿]")


def _log(msg: str) -> None:
    print(f"[readingrhythms] {msg}", flush=True)


def matches(url: str) -> bool:
    return "readingrhythms.co" in url


def parse_listing(html: str) -> list[str]:
    """Absolute URLs of Bay Area event pages on /events, in page order."""
    urls = []
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        m = _EVENT_PATH_RE.match(a["href"])
        if not m or not is_bay_area(m.group(1).replace("-", " ")):
            continue
        url = urljoin(SITE, a["href"])
        if url not in urls:
            urls.append(url)
    return urls


def _json_ld_event(html: str) -> dict | None:
    for script in BeautifulSoup(html, "html.parser").find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and node.get("@type") == "Event":
                return node
    return None


def _fix_mojibake(text: str) -> str:
    """The site double-encodes UTF-8 (e.g. a zero-width space arrives as
    'â\\x80\\x8b'). Re-decode when that's what happened; else leave it alone."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _clean_description(text: str | None) -> str | None:
    if not text:
        return None
    text = _INVISIBLE_RE.sub("", _fix_mojibake(text))
    text = _MARKDOWN_RE.sub(r"\1", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _location(event: dict) -> str | None:
    place = event.get("location") or {}
    if isinstance(place, str):
        return place.strip() or None
    name = (place.get("name") or "").strip()
    address = place.get("address") or ""
    if isinstance(address, dict):
        address = ", ".join(str(address[k]) for k in ("streetAddress", "addressLocality", "addressRegion",
                                                      "postalCode") if address.get(k))
    address = address.strip()
    parts = ([name] if name and name not in address else []) + ([address] if address else [])
    return ", ".join(parts) or None


def parse_event(html: str, url: str) -> RawEvent | None:
    """RawEvent from an event page's JSON-LD, or None (no Event / not Bay Area)."""
    event = _json_ld_event(html)
    if not event or not event.get("name") or not event.get("startDate"):
        return None
    try:
        start = datetime.fromisoformat(event["startDate"].replace("Z", "+00:00"))
    except ValueError:
        return None
    if start.tzinfo is None:
        return None
    location = _location(event)
    if not is_bay_area(location):
        return None
    image = event.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    return RawEvent(
        title=_fix_mojibake(event["name"]).strip(),
        start_time=start.astimezone(timezone.utc),
        location=location,
        url=url,
        description=_clean_description(event.get("description")),
        image_url=image if isinstance(image, str) else None,
    )


def _get(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    urls = parse_listing(_get(url))
    _log(f"{len(urls)} Bay Area event pages on {url}")
    events = []
    for event_url in urls:
        try:
            raw = parse_event(_get(event_url), event_url)
        except Exception as e:  # one bad page shouldn't sink the source
            _log(f"event page failed {event_url}: {type(e).__name__}: {e}")
            continue
        if raw:
            events.append(raw)
    _log(f"done: {len(events)} events")
    return events
