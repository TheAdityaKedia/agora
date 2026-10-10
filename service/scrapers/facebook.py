"""Shared Facebook Page events scraping helpers.

A public Page's events tab (``facebook.com/<page>/upcoming_hosted_events``) and
each event page (``facebook.com/events/<id>``) load logged-out, and both embed
the data as Relay JSON in the HTML — no browser needed:

  - listing: one ``{"__typename":"Event", ...}`` node per upcoming event (id,
    name, is_canceled, place name + city, url). Facebook says when there's a
    next page; small Pages fit in the first one, so we don't paginate.
  - event page: an ``"event":{...}`` object (start_timestamp in UTC epoch
    seconds), plus ``"event_description":{"text": ...}`` and the
    venue's ``"one_line_address"`` elsewhere in the page.

We pull each object out with ``json.JSONDecoder.raw_decode`` at its key rather
than walking Facebook's (huge, shifting) payload structure.

Fetching: Facebook's robots.txt disallows all crawlers and it walls datacenter
IPs (GitHub runners) behind a login, so CI fetches go through Zyte's cheap
``httpResponseBody`` tier (scrapers/zyte.py; ~8s and one credit per page —
browser rendering isn't needed). Without ``ZYTE_API_KEY`` (local runs) we fetch
directly with browser-like headers, which works from a residential IP; a bare
``requests`` UA gets HTTP 400. Owner OK'd scraping Facebook this way
(2026-10-02) for Pages that publish only there.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import requests

from scrapers import zyte
from scrapers.base import RawEvent
from scrapers.bay_area import is_bay_area
from scrapers.browser import BROWSER_UA

REQUEST_TIMEOUT = 25
HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

_DECODER = json.JSONDecoder()
_EVENT_NODE_RE = re.compile(r'\{"__typename":"Event","id":"\d+"')


def _log(msg: str) -> None:
    print(f"[facebook] {msg}", flush=True)


def listing_url(page: str) -> str:
    return f"https://www.facebook.com/{page}/upcoming_hosted_events"


def event_url(event_id: str) -> str:
    return f"https://www.facebook.com/events/{event_id}/"


def _decode_at(html: str, index: int):
    try:
        return _DECODER.raw_decode(html, index)[0]
    except ValueError:
        return None


def _value_after(html: str, key: str):
    """JSON value following the first ``"key":`` in the page, or None."""
    i = html.find(f'"{key}":')
    return None if i < 0 else _decode_at(html, i + len(key) + 3)


def parse_listing(html: str) -> list[dict]:
    """Upcoming Event nodes on a Page's events tab (cancelled ones too: the
    site shows them as cancelled), deduped by id."""
    events: dict[str, dict] = {}
    for m in _EVENT_NODE_RE.finditer(html):
        node = _decode_at(html, m.start())
        if isinstance(node, dict) and node.get("name"):
            events.setdefault(node["id"], node)
    return list(events.values())


def listing_city(node: dict) -> str | None:
    place = node.get("event_place") or {}
    return ((place.get("location") or {}).get("reverse_geocode") or {}).get("city")


def _event_object(html: str, event_id: str) -> dict | None:
    """The ``"event":{...}`` object for this event (the one with a start time)."""
    for m in re.finditer(r'"event":\{', html):
        obj = _decode_at(html, m.end() - 1)
        if isinstance(obj, dict) and obj.get("id") == event_id and obj.get("start_timestamp"):
            return obj
    return None


def parse_event(html: str, event_id: str) -> RawEvent | None:
    """RawEvent from an event page (status "cancelled" if it's cancelled), or
    None if online or unparseable."""
    event = _event_object(html, event_id)
    if not event or event.get("is_online"):
        return None
    place = (event.get("event_place") or {}).get("name")
    address = _value_after(html, "one_line_address")
    if isinstance(address, str):
        address = re.sub(r"(-\d{4})?, United States$", "", address)
    location = ", ".join(p for p in (place, address if isinstance(address, str) else None) if p) or None
    description = _value_after(html, "event_description")
    description = (description or {}).get("text") if isinstance(description, dict) else None
    return RawEvent(
        title=event["name"].strip(),
        start_time=datetime.fromtimestamp(event["start_timestamp"], tz=timezone.utc),
        location=location,
        url=event_url(event_id),
        description=(description or "").strip() or None,
        # No image: fbcdn cover URLs are signed and expire within days (`oe=`),
        # saved rows are never refreshed, and the site has no broken-image fallback.
        image_url=None,
        status="cancelled" if event.get("is_canceled") else None,
    )


def fetch_html(url: str) -> str:
    if zyte.is_configured():
        return zyte.fetch_html(url, render=False, log=_log)
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def scrape_page(page: str) -> list[RawEvent]:
    """All upcoming Bay Area events hosted by a Facebook Page."""
    nodes = parse_listing(fetch_html(listing_url(page)))
    local = [n for n in nodes if is_bay_area(listing_city(n))]
    _log(f"{page}: {len(nodes)} upcoming events, {len(local)} in the Bay Area "
         f"({'zyte' if zyte.is_configured() else 'direct'})")
    events = []
    for node in local:
        try:
            raw = parse_event(fetch_html(event_url(node["id"])), node["id"])
        except Exception as e:  # one bad page shouldn't sink the source
            _log(f"event page failed {node['id']}: {type(e).__name__}: {e}")
            continue
        if raw:
            events.append(raw)
    _log(f"{page}: done, {len(events)} events")
    return events
