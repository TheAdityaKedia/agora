"""Commonwealth Club events scraper.

The Commonwealth Club (a major SF public-affairs forum with a dense talks
calendar) runs Drupal. Its ``/events`` listing paginates with ``?page=N`` and
links to per-event detail pages, and each detail page embeds a clean schema.org
``Event`` in a JSON-LD ``@graph`` (name, start, location, image, description).

We walk the listing pages to collect detail URLs, then fetch each detail page
and parse its JSON-LD. NOTE: the JSON-LD ``startDate`` is in **UTC** with no
offset (e.g. a 5:30 PM PDT talk is ``2026-09-25T00:30:00``), so we attach UTC
directly rather than treating it as local time.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "commonwealthclub.org"
NAME = "Commonwealth Club"
BASE_URL = "https://www.commonwealthclub.org"
EVENTS_URL = f"{BASE_URL}/events"
REQUEST_TIMEOUT = 25
MAX_PAGES = 20          # listing safety bound
# The site rate-limits, so fetch gently: few workers, retry with backoff.
DETAIL_WORKERS = 3
DETAIL_LOG_EVERY = 25
FETCH_ATTEMPTS = 3


def _log(msg: str) -> None:
    print(f"[commonwealthclub] {msg}", flush=True)


def matches(url: str) -> bool:
    return "commonwealthclub.org" in url


def parse_listing_links(html: str) -> list[str]:
    """Absolute detail-page URLs from one listing page, deduped in order."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0].split("#")[0]
        # Detail pages look like /events/2026-09-24/<slug>
        if not re.match(r"^/events/\d{4}-\d{2}-\d{2}/.+", href):
            continue
        abs_url = BASE_URL + href
        if abs_url not in seen:
            seen.add(abs_url)
            urls.append(abs_url)
    return urls


def _event_ld(html: str) -> dict | None:
    """The schema.org Event object from a detail page's JSON-LD, or None."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        graph = data.get("@graph") if isinstance(data, dict) else None
        candidates = graph if isinstance(graph, list) else [data]
        for obj in candidates:
            if isinstance(obj, dict) and obj.get("@type") == "Event":
                return obj
    return None


def _location(loc) -> str | None:
    if not isinstance(loc, dict):
        return loc if isinstance(loc, str) else None
    name = (loc.get("name") or "").strip()
    addr = loc.get("address")
    city = ""
    if isinstance(addr, dict):
        city = (addr.get("addressLocality") or "").strip()
    parts = [p for p in (name, city) if p]
    return ", ".join(parts) or None


def parse_detail(html: str) -> RawEvent | None:
    """Parse a detail page's JSON-LD Event into a RawEvent (start_time in UTC)."""
    obj = _event_ld(html)
    if obj is None:
        return None
    name = (obj.get("name") or "").strip()
    start = obj.get("startDate")
    if not (name and start):
        return None
    try:
        # startDate is UTC without an offset — attach UTC, don't localize.
        start_time = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    image = obj.get("image")
    if isinstance(image, dict):
        image = image.get("url")
    elif isinstance(image, list):
        image = image[0] if image else None
    desc = obj.get("description")
    if isinstance(desc, str):
        desc = re.sub(r"\s+", " ", desc).strip() or None
    return RawEvent(
        title=name,
        start_time=start_time,
        location=_location(obj.get("location")) or NAME,
        url=obj.get("url") or None,
        description=desc,
        image_url=image if isinstance(image, str) else None,
    )


def _get(url: str) -> str:
    """Fetch with retries — the site intermittently drops/rate-limits requests."""
    last_exc: Exception | None = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            last_exc = e
            if attempt < FETCH_ATTEMPTS - 1:
                time.sleep(1.5 * (attempt + 1))
    raise last_exc


def _collect_detail_urls() -> list[str]:
    """Walk listing pages until one adds no new detail links."""
    urls: list[str] = []
    seen: set[str] = set()
    for page in range(MAX_PAGES):
        try:
            html = _get(f"{EVENTS_URL}?page={page}")
        except requests.RequestException as e:
            _log(f"listing page {page} failed: {e}")
            break
        new = [u for u in parse_listing_links(html) if u not in seen]
        if not new:
            break  # plateau (later pages repeat a persistent link) → done
        for u in new:
            seen.add(u)
            urls.append(u)
        _log(f"page {page}: {len(new)} new events")
    return urls


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    detail_urls = _collect_detail_urls()
    _log(f"enriching {len(detail_urls)} events with {DETAIL_WORKERS} workers")
    events: list[RawEvent] = []

    def fetch_one(u: str) -> RawEvent | None:
        try:
            return parse_detail(_get(u))
        except requests.RequestException as e:
            _log(f"detail fetch failed for {u}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        for i, ev in enumerate(pool.map(fetch_one, detail_urls), 1):
            if ev is not None:
                events.append(ev)
            if i % DETAIL_LOG_EVERY == 0:
                _log(f"  {i}/{len(detail_urls)} fetched")
    _log(f"done: {len(events)} events")
    return events
