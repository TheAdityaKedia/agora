"""City Arts & Lectures events scraper.

City Arts & Lectures (SF's marquee onstage-conversation series at the Sydney
Goldstein Theater) runs WordPress with a custom ``/event/<slug>/`` page per
show. The pages carry no JSON-LD, but each has a ``.date`` element with a
Pacific-local date/time, a ``.location`` ("Venue: …"), and an ``.event-meta``
subtitle. We collect the event links from ``/events/`` and parse each detail.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "cityarts.net"
NAME = "City Arts & Lectures"
BASE_URL = "https://www.cityarts.net"
EVENTS_URL = f"{BASE_URL}/events/"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 25
DETAIL_WORKERS = 5

_EVENT_LINK_RE = re.compile(r"^https?://www\.cityarts\.net/event/[^/]+/?$")
_WEEKDAY_RE = re.compile(
    r"\b(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday),", re.IGNORECASE
)


def _log(msg: str) -> None:
    print(f"[cityarts] {msg}", flush=True)


def matches(url: str) -> bool:
    return "cityarts.net" in url


def parse_listing_links(html: str) -> list[str]:
    """Absolute /event/<slug>/ URLs from the events page, deduped in order."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0]
        if _EVENT_LINK_RE.match(href) and href not in seen:
            seen.add(href)
            urls.append(href)
    return urls


def _parse_start(date_text: str) -> datetime | None:
    """Parse '.date' text like 'Friday, October 23, 2026 7:30pm Pacific Time'."""
    cleaned = re.sub(r"\s*Pacific Time\s*$", "", date_text, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    try:
        naive = datetime.strptime(cleaned, "%A, %B %d, %Y %I:%M%p")
    except ValueError:
        return None
    return naive.replace(tzinfo=SOURCE_TZ).astimezone(timezone.utc)


def parse_detail(html: str, url: str | None = None) -> RawEvent | None:
    soup = BeautifulSoup(html, "html.parser")
    date_el = soup.select_one(".date")
    if not date_el:
        return None
    start_time = _parse_start(date_el.get_text(" ", strip=True))
    if start_time is None:
        return None

    # Title from <title> ("Speaker | City Arts & Lectures").
    raw_title = soup.title.get_text(strip=True) if soup.title else ""
    title = raw_title.split("|")[0].strip()
    if not title:
        return None

    # Subtitle: the .event-meta text before the date line.
    description = None
    meta = soup.select_one(".event-meta")
    if meta:
        text = re.sub(r"\s+", " ", meta.get_text(" ", strip=True)).strip()
        m = _WEEKDAY_RE.search(text)
        description = (text[: m.start()].strip() if m else text) or None

    location = NAME
    loc_el = soup.select_one(".location")
    if loc_el:
        loc = re.sub(r"^\s*Venue:\s*", "", loc_el.get_text(" ", strip=True)).strip()
        if loc:
            location = loc

    return RawEvent(
        title=title,
        start_time=start_time,
        location=location,
        url=url,
        description=description,
        image_url=None,
    )


def _get(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    try:
        listing = _get(EVENTS_URL)
    except requests.RequestException as e:
        _log(f"events page fetch failed: {e}")
        return []
    detail_urls = parse_listing_links(listing)
    _log(f"{len(detail_urls)} events linked; enriching with {DETAIL_WORKERS} workers")
    events: list[RawEvent] = []

    def fetch_one(u: str) -> RawEvent | None:
        try:
            return parse_detail(_get(u), u)
        except requests.RequestException as e:
            _log(f"detail fetch failed for {u}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        for ev in pool.map(fetch_one, detail_urls):
            if ev is not None:
                events.append(ev)
    _log(f"done: {len(events)} events")
    return events
