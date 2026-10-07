"""Stanford Live events scraper (via Spektrix).

Stanford University's performing-arts presenter (Bing Concert Hall, Frost
Amphitheater, Dinkelspiel, Memorial Auditorium) plus the Department of Music's
concerts, all ticketed through Spektrix. Thin wrapper around
scrapers/spektrix.py; one event per performance. Kronos Quartet's Stanford
dates also appear here; cross-source dedup merges them.

The Spektrix catalog has no images and names no hall, so each show page (one
fetch per unique page, in parallel) contributes its header image
(``img.event-header__image``; the page's other images belong to related
shows) and its hall (``meta[name=venue_title]``), which HALLS maps to a street
address the venue resolver can place. Events without a page, or with an
unmapped hall, fall back to the campus.

Dropped: "Student Lottery Winners" allocations and "Student Matinee" school
shows (not public; they also carry no web page). Other events without a page
link to the calendar.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

from scrapers import spektrix
from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "live.stanford.edu"
NAME = "Stanford Live"
API_BASE = "https://ticketing.purchase.live.stanford.edu/stanfordlive/api/v3"
EVENTS_URL = "https://live.stanford.edu/calendar"
ADDRESS = "Stanford University, Stanford, CA 94305"
# venue_title on a show page -> address. "The Studio" is a room inside Bing:
# the "Venue — Room" form keeps it from becoming a venue of its own that
# Bing Concert Hall's shows would then alias to (same building on the map).
HALLS = {
    "bing concert hall": "Bing Concert Hall, 327 Lasuen St, Stanford, CA 94305",
    "the studio": "Bing Concert Hall — Bing Studio, 327 Lasuen St, Stanford, CA 94305",
    "frost amphitheater": "Frost Amphitheater, 351 Lasuen St, Stanford, CA 94305",
    "memorial auditorium": "Memorial Auditorium, 551 Jane Stanford Way, Stanford, CA 94305",
    "memorial church": "Stanford Memorial Church, 450 Jane Stanford Way, Stanford, CA 94305",
    "dinkelspiel auditorium": "Dinkelspiel Auditorium, 471 Lagunita Dr, Stanford, CA 94305",
}

# From GitHub's runners, 5 parallel fetches lost 14 of ~60 show pages
# (2026-10-07) that all load fine one at a time; fewer workers plus retries.
PAGE_WORKERS = 3
PAGE_ATTEMPTS = 3
RETRY_BACKOFF = 2.0  # seconds, doubled per retry
REQUEST_TIMEOUT = 25
_NOT_PUBLIC_RE = re.compile(r"\bstudent (lottery|matinee)\b", re.I)


def matches(url: str) -> bool:
    return "live.stanford.edu" in url


def public(events: list[RawEvent]) -> list[RawEvent]:
    """Drop non-public allocations; link page-less events to the calendar. Pure."""
    out = []
    for e in events:
        if _NOT_PUBLIC_RE.search(e.title):
            continue
        e.url = e.url or EVENTS_URL
        out.append(e)
    return out


def header_image(html: str) -> str | None:
    """The show's own header image on a live.stanford.edu page. Pure."""
    img = BeautifulSoup(html, "html.parser").select_one("img.event-header__image")
    if img is None:
        return None
    src = img.get("src") or img.get("data-src")
    if not src and img.get("srcset"):
        src = img["srcset"].split(",")[0].strip().split(" ")[0]  # first candidate
    return src or None


def hall_location(html: str) -> str | None:
    """The show's hall on a live.stanford.edu page, as an address. A hall
    missing from HALLS is still placed on campus. Pure."""
    meta = BeautifulSoup(html, "html.parser").select_one("meta[name=venue_title]")
    hall = (meta.get("content") or "").strip() if meta else ""
    if not hall:
        return None
    return HALLS.get(hall.lower(), f"{hall}, {ADDRESS}")


def _fetch_page(page_url: str, get=requests.get, sleep=time.sleep) -> tuple[str | None, str | None]:
    """A show page's (image, hall address), retrying transient failures;
    (None, None) when every attempt fails."""
    for attempt in range(PAGE_ATTEMPTS):
        try:
            r = get(page_url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return header_image(r.text), hall_location(r.text)
        except requests.RequestException as e:
            error = e
            if attempt + 1 < PAGE_ATTEMPTS:
                sleep(RETRY_BACKOFF * 2 ** attempt)
    print(f"[stanfordlive] show page failed: {page_url} ({type(error).__name__}: {error})", flush=True)
    return None, None


def add_page_details(events: list[RawEvent], fetch=_fetch_page) -> None:
    """Fill image and hall from each show page, fetched once per page."""
    pages = sorted({e.url for e in events
                    if e.url and e.url != EVENTS_URL and "live.stanford.edu" in e.url})
    with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pool:
        details = dict(zip(pages, pool.map(fetch, pages)))
    failed = sum(1 for d in details.values() if d == (None, None))
    if failed:
        print(f"[stanfordlive] {failed} of {len(pages)} show pages gave no image or hall", flush=True)
    for e in events:
        image, location = details.get(e.url, (None, None))
        e.image_url = e.image_url or image
        e.location = location or e.location


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    events = public(spektrix.scrape(API_BASE, fallback_location=ADDRESS, tag="stanfordlive"))
    add_page_details(events)
    return events
