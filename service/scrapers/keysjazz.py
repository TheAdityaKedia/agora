"""Keys Jazz Bistro events scraper (North Beach SF jazz club).

Keys Jazz Bistro runs WordPress, but NOT The Events Calendar (Tribe) — its
``/wp-json/tribe/...`` route 404s (``rest_no_route``); the site uses the
"Simple Events" plugin, whose REST routes require auth. The plugin does,
however, emit clean schema.org JSON-LD on each ``/event/<slug>/`` page: an
array with **one ``Event`` block per showtime** (a two-set night yields two
blocks with distinct ISO ``startDate``s), plus location, image, price and a
short description. That JSON-LD is the richest anonymously-available source
(structured-JSON-API rung is closed), so this scraper:

  1. reads the venue's own ``/upcoming-shows/`` listing to discover show URLs;
  2. fetches each ``/event/<slug>/`` page and maps every JSON-LD ``Event``
     block to one RawEvent (per-performance, per the "one event per showing"
     rule — the data is genuinely per-occurrence, not fabricated).

Fetching goes through a headless browser: the site 429s bare ``requests`` from
local/CI IPs but renders fine in Chromium. The pure ``parse_*`` functions take
already-fetched HTML so they're testable offline.
"""
from __future__ import annotations

import html as _html
import json
import re
from datetime import datetime, timezone
from typing import Callable

from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import RateLimited, browser_context, load_page_html


SOURCE = "keysjazzbistro.com"
NAME = "Keys Jazz Bistro"
SITE_BASE = "https://keysjazzbistro.com"
LISTING_URL = "https://keysjazzbistro.com/upcoming-shows/"
FALLBACK_LOCATION = "Keys Jazz Bistro, 498 Broadway, San Francisco, CA 94133"

_RL_BACKOFF_S = 30.0
_TRUNCATION_RE = re.compile(r"\s*\[(?:…|\.\.\.)\]\s*$")


def _log(msg: str) -> None:
    print(f"[keysjazz] {msg}", flush=True)


def matches(url: str) -> bool:
    return "keysjazzbistro.com" in url


# --- pure parsers ------------------------------------------------------------

def _base_url(url: str | None) -> str | None:
    """Strip the query/fragment (e.g. ?se-date=NNN) so every showtime of a
    run shares the one canonical show URL."""
    if not url:
        return None
    return url.split("?", 1)[0].split("#", 1)[0]


def _clean_text(raw: str | None) -> str | None:
    if not raw:
        return None
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    text = _html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _TRUNCATION_RE.sub("", text).strip()  # drop trailing "[…]" excerpt marker
    return text or None


def _to_utc(value: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime with offset (e.g. 2026-10-03T19:00:00-07:00)
    and normalize to UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def _location(place: object) -> str | None:
    if not isinstance(place, dict):
        return None
    parts = [str(place.get("name") or "").strip()]
    addr = place.get("address")
    if isinstance(addr, dict):
        parts += [str(addr.get(k) or "").strip()
                  for k in ("streetAddress", "addressLocality", "addressRegion")]
    parts = [_html.unescape(p) for p in parts if p]
    return ", ".join(parts) or None


def _first_image(image: object) -> str | None:
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, str) and image.strip():
        return image.strip()
    return None


def _iter_ld_events(html: str):
    """Yield every schema.org ``Event`` dict from a page's JSON-LD blocks."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        text = script.string or script.get_text()
        if not text:
            continue
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "Event":
                yield item


def parse_event_page(html: str, page_url: str) -> list[RawEvent]:
    """Map a show page's JSON-LD Event blocks to RawEvents (one per showtime).

    Pure: operates on already-fetched HTML. Dedupes identical (title,
    start_time) blocks defensively.
    """
    events: list[RawEvent] = []
    seen: set[tuple[str, datetime]] = set()
    for item in _iter_ld_events(html):
        title = _html.unescape((item.get("name") or "").strip())
        start_time = _to_utc(item.get("startDate"))
        if not (title and start_time):
            continue
        key = (title, start_time)
        if key in seen:
            continue
        seen.add(key)
        offer = item.get("offers")
        offer_url = offer.get("url") if isinstance(offer, dict) else None
        events.append(RawEvent(
            title=title,
            start_time=start_time,
            location=_location(item.get("location")) or FALLBACK_LOCATION,
            url=_base_url(offer_url) or _base_url(page_url),
            description=_clean_text(item.get("description")),
            image_url=_first_image(item.get("image")),
        ))
    return events


def parse_listing(html: str) -> list[str]:
    """Extract unique ``/event/<slug>/`` show URLs from a listing page, in
    document order."""
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/event/" not in href:
            continue
        base = _base_url(href)
        # keep only individual show pages: .../event/<slug>/
        if not base or not re.search(r"/event/[^/]+/?$", base):
            continue
        if base not in seen:
            seen.add(base)
            urls.append(base)
    return urls


# --- fetch orchestration -----------------------------------------------------

def scrape_with_fetcher(fetch: Callable[[str], str | None]) -> list[RawEvent]:
    """Discover shows from the listing, then parse each show page.

    ``fetch(url) -> html | None`` isolates I/O so the flow is unit-testable.
    A None (missing/blocked page) is skipped.
    """
    listing_html = fetch(LISTING_URL)
    if not listing_html:
        _log("listing fetch failed; no events")
        return []
    show_urls = parse_listing(listing_html)
    _log(f"listing: {len(show_urls)} shows")

    events: list[RawEvent] = []
    seen: set[tuple[str | None, datetime]] = set()
    for i, url in enumerate(show_urls, 1):
        page = fetch(url)
        if not page:
            continue
        for ev in parse_event_page(page, url):
            key = (ev.url, ev.start_time)
            if key in seen:
                continue
            seen.add(key)
            events.append(ev)
        if i % 10 == 0:
            _log(f"  fetched {i}/{len(show_urls)} shows, {len(events)} events so far")
    _log(f"done: {len(events)} events from {len(show_urls)} shows")
    return events


def scrape(url: str = LISTING_URL) -> list[RawEvent]:
    """Fetch + parse all upcoming Keys Jazz Bistro shows via a headless browser."""
    with browser_context() as context:
        def fetch(u: str) -> str | None:
            for attempt in range(2):
                try:
                    return load_page_html(context, u, wait_until="load", timeout=30000)
                except RateLimited as e:
                    _log(f"rate-limited (HTTP {e.status}) at {u}; backing off")
                    import time
                    time.sleep(_RL_BACKOFF_S)
                except Exception as e:  # pragma: no cover - network noise
                    _log(f"fetch error at {u}: {type(e).__name__}: {e}")
                    return None
            return None

        return scrape_with_fetcher(fetch)
