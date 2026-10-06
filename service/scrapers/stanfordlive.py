"""Stanford Live events scraper (via Spektrix).

Stanford University's performing-arts presenter (Bing Concert Hall, Frost
Amphitheater, Dinkelspiel, Memorial Auditorium) plus the Department of Music's
concerts, all ticketed through Spektrix. Thin wrapper around
scrapers/spektrix.py; one event per performance. The API names no hall, so
events fall back to the campus. Kronos Quartet's Stanford dates also appear
here; cross-source dedup merges them.

Images: the Spektrix catalog has none, so each show page (one fetch per
unique page, in parallel) contributes its header image
(``img.event-header__image``). Only the header is read; the page's other
images belong to related shows.

Dropped: "Student Lottery Winners" allocations and "Student Matinee" school
shows (not public; they also carry no web page). Other events without a page
link to the calendar.
"""
import re
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
ADDRESS = "Stanford Live, Stanford University, Stanford, CA 94305"

PAGE_WORKERS = 5
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


def _fetch_image(page_url: str) -> str | None:
    try:
        r = requests.get(page_url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException:
        return None
    return header_image(r.text)


def add_images(events: list[RawEvent], fetch=_fetch_image) -> None:
    """Fill missing images from each show page, fetched once per page."""
    pages = sorted({e.url for e in events
                    if not e.image_url and e.url and e.url != EVENTS_URL and "live.stanford.edu" in e.url})
    with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pool:
        images = dict(zip(pages, pool.map(fetch, pages)))
    for e in events:
        if not e.image_url and images.get(e.url):
            e.image_url = images[e.url]


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    events = public(spektrix.scrape(API_BASE, fallback_location=ADDRESS, tag="stanfordlive"))
    add_images(events)
    return events
