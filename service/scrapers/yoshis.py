"""Yoshi's (Oakland) events scraper — legendary jazz club + restaurant.

Yoshi's runs its own calendar at https://yoshis.com/events and sells tickets
through etix.com. There is no JSON-LD, no JSON API, and no ICS feed — plain
`requests` returns fully server-rendered HTML — so this is a two-step DOM
scrape (listing → detail), which sits below the structured rungs of the
data-source ladder but is the only source Yoshi's exposes.

  1. The listing (`/events`) is a `<ul class="eventListings">` of `<li>` cards,
     one per *showing*: a `p.date` ("September 24, 2026", with year), a title +
     detail-page link (`h2 > a`), and a thumbnail. A multi-night run repeats as
     several cards (and a two-set night appears twice), so we collapse the
     listing to the set of unique detail-page URLs and get the authoritative
     per-performance data from each detail page.

  2. The detail page (`/events/buy-tickets/<slug>/detail`) is the rich source.
     It comes in two layouts:
       - Single performance: `.event-meat` carries `.bigdate`
         ("Sun September 27, 2026"), a `.topline` subtitle, `h1.bigname` title,
         and `.bigtime` ("Doors: 6:30 PM   Show: 7:00 PM").
       - Multi performance: a `ul.event-full-list` with one `li.event-indv` per
         showing, each an `h3` date + a `p.topline` "Doors: … / Show: …" — one
         RawEvent per `li`, so a run becomes one event per night/set.
     Both layouts end `.event-details` with a single class-less `<div>` holding
     the show's description; the `.event-img` is the poster.

The venue is America/Los_Angeles; every start_time is normalized to UTC. The
RawEvent.url is always the yoshis.com show page (never the etix checkout link).
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "yoshis.com"
NAME = "Yoshi's"
BASE_URL = "https://yoshis.com"
EVENTS_URL = "https://yoshis.com/events"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "Yoshi's, 510 Embarcadero West, Oakland, CA 94607"

REQUEST_TIMEOUT = 25
# Detail pages are static and un-guarded, so parallel fetches are safe/fast.
DETAIL_WORKERS = 5
DETAIL_LOG_EVERY = 15
# Fallback show time when a detail page omits a parseable time (rare).
DEFAULT_HOUR, DEFAULT_MINUTE = 20, 0

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}
# "September 27, 2026" (optionally prefixed by a weekday, e.g. "Sun September …")
_DATE_RE = re.compile(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})")
# "Show: 7:00 PM" / "Show: 7:30PM"; also matches a bare "6:30 PM".
_SHOW_TIME_RE = re.compile(r"Show:\s*(\d{1,2}):(\d{2})\s*([APap][Mm])")
_ANY_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([APap][Mm])")


def _log(msg: str) -> None:
    print(f"[yoshis] {msg}", flush=True)


def matches(url: str) -> bool:
    return "yoshis.com" in url


def _parse_date(text: str) -> tuple[int, int, int] | None:
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if month is None:
        return None
    return int(m.group(3)), month, int(m.group(2))


def _parse_show_time(text: str) -> tuple[int, int]:
    """Return (hour, minute) for a showing — the 'Show:' time, else the first
    time in the string, else the venue default."""
    m = _SHOW_TIME_RE.search(text or "") or _ANY_TIME_RE.search(text or "")
    if not m:
        return DEFAULT_HOUR, DEFAULT_MINUTE
    hour = int(m.group(1)) % 12
    if m.group(3).lower() == "pm":
        hour += 12
    return hour, int(m.group(2))


def _to_utc(ymd: tuple[int, int, int], hm: tuple[int, int]) -> datetime | None:
    try:
        return datetime(ymd[0], ymd[1], ymd[2], hm[0], hm[1],
                        tzinfo=SOURCE_TZ).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_listing(html: str) -> list[str]:
    """Return the unique detail-page URLs from a Yoshi's listing page,
    preserving first-seen order. Multi-night runs collapse to one URL."""
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for a in soup.select("ul.eventListings > li h2 a[href]"):
        href = a.get("href")
        if not href:
            continue
        url = urljoin(BASE_URL, href)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _description(details, subtitle: str | None) -> str | None:
    """The show blurb: the single class-less <div> that closes .event-details,
    optionally prefixed with the genre subtitle for tagging context."""
    body = None
    if details is not None:
        classless = [d for d in details.find_all("div", recursive=False)
                     if not d.get("class")]
        if classless:
            text = classless[-1].get_text(" ", strip=True)
            body = " ".join(text.split()) or None
    if body and subtitle and subtitle not in body:
        return f"{subtitle} · {body}"
    return body or subtitle or None


def parse_detail(html: str, url: str) -> list[RawEvent]:
    """Parse a Yoshi's detail page into one RawEvent per performance.

    Handles both the single-performance (`.bigdate`/`.bigtime`) and
    multi-performance (`ul.event-full-list`) layouts.
    """
    soup = BeautifulSoup(html, "html.parser")
    details = soup.select_one(".event-details")

    title_tag = soup.select_one("h1.bigname")
    title = title_tag.get_text(strip=True) if title_tag else None
    if not title:
        return []

    subtitle_tag = None
    meat = soup.select_one(".event-meat")
    if meat is not None:
        subtitle_tag = meat.select_one(".topline")
    subtitle = subtitle_tag.get_text(" ", strip=True) if subtitle_tag else None

    img_tag = soup.select_one(".event-img")
    image_url = (urljoin(BASE_URL, img_tag["src"])
                 if img_tag and img_tag.get("src") else None)

    description = _description(details, subtitle)

    # Collect (date, time) pairs per performance.
    perfs: list[tuple[tuple[int, int, int], tuple[int, int]]] = []
    indv = soup.select("ul.event-full-list li.event-indv")
    if indv:  # multi-performance layout
        for li in indv:
            date_tag = li.select_one("h3")
            time_tag = li.select_one("p.topline")
            ymd = _parse_date(date_tag.get_text(strip=True)) if date_tag else None
            if not ymd:
                continue
            hm = _parse_show_time(time_tag.get_text(" ", strip=True) if time_tag else "")
            perfs.append((ymd, hm))
    else:  # single-performance layout
        # Scope to .event-meat: a mailing-list widget elsewhere on the page
        # reuses the `.bigdate` class ("Sign Up for Yoshi's Weekly Mailing
        # List"), so an unscoped select_one grabs that decoy instead of the date.
        scope = meat if meat is not None else soup
        date_tag = scope.select_one(".bigdate")
        time_tag = scope.select_one(".bigtime")
        ymd = _parse_date(date_tag.get_text(strip=True)) if date_tag else None
        if ymd:
            hm = _parse_show_time(time_tag.get_text(" ", strip=True) if time_tag else "")
            perfs.append((ymd, hm))

    events: list[RawEvent] = []
    for ymd, hm in perfs:
        start = _to_utc(ymd, hm)
        if start is None:
            continue
        events.append(RawEvent(
            title=title,
            start_time=start,
            location=VENUE,
            url=url,
            description=description,
            image_url=image_url,
        ))
    return events


def _fetch(url: str) -> str | None:
    """Fetch a page; return HTML or None on any error/non-200."""
    try:
        resp = requests.get(url, headers={"User-Agent": BROWSER_UA},
                            timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            _log(f"fetch {url}: HTTP {resp.status_code}")
            return None
        return resp.text
    except requests.RequestException as e:
        _log(f"fetch {url}: {type(e).__name__}: {e}")
        return None


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    """Fetch the Yoshi's listing, then each show's detail page, expanding runs
    into one RawEvent per performance."""
    _log(f"phase 1: fetching listing {url}")
    listing = _fetch(url)
    if not listing:
        _log("listing fetch failed; no events")
        return []
    detail_urls = parse_listing(listing)
    _log(f"phase 1: {len(detail_urls)} unique show pages")

    _log(f"phase 2: fetching {len(detail_urls)} detail pages "
         f"({DETAIL_WORKERS} workers)")
    events: list[RawEvent] = []
    seen: set[tuple[str, datetime]] = set()
    t0 = time.monotonic()
    done = 0
    total = len(detail_urls)
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        futures = {pool.submit(_fetch, u): u for u in detail_urls}
        for fut in as_completed(futures):
            done += 1
            u = futures[fut]
            html = fut.result()
            if html:
                for ev in parse_detail(html, u):
                    key = (ev.url, ev.start_time)
                    if key in seen:
                        continue
                    seen.add(key)
                    events.append(ev)
            if done % DETAIL_LOG_EVERY == 0 or done == total:
                _log(f"detail {done}/{total} fetched, {len(events)} performances "
                     f"({time.monotonic() - t0:.0f}s)")
    _log(f"done: {len(events)} performances from {total} shows")
    return events
