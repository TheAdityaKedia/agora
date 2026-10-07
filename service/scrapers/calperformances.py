"""Cal Performances (UC Berkeley) events scraper.

UC Berkeley's presenter of music, dance, theater and talks at Zellerbach
Hall, Zellerbach Playhouse, Hertz Hall and other campus halls. WordPress
(``cp_event`` posts) with no usable API, so:

1. Walk the ``/events/page/N/`` listing (10 cards a page) for event pages.
   The listing runs back through past seasons (~250 pages), so only links
   in the current or next season (``/events/2026-27/…``) are kept, and the
   walk stops at the first listing page with none. Past starts are dropped.
2. Read each event page's "Add to Calendar" widget (``.addeventatc``): one
   block per performance with ``.start`` ("10/28/2026 07:30 pm"),
   ``.timezone`` and ``.location`` (the hall). Pages repeat blocks (desktop +
   mobile), so performances are de-duplicated by start.
3. ``og:description`` and ``og:image`` are per event on this site (checked
   2026-10), so they supply the blurb and picture.
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


SOURCE = "calperformances.org"
NAME = "Cal Performances"
BASE_URL = "https://calperformances.org"
EVENTS_URL = "https://calperformances.org/events/"
CAMPUS = "UC Berkeley, Berkeley, CA 94720"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 30
MAX_PAGES = 25
PAGE_WORKERS = 5


def matches(url: str) -> bool:
    return "calperformances.org" in url


def seasons(today) -> tuple[str, str]:
    """Current and next season slugs ("2026-27", "2027-28"); seasons start in July. Pure."""
    y = today.year if today.month >= 7 else today.year - 1
    return f"{y}-{(y + 1) % 100:02d}", f"{y + 1}-{(y + 2) % 100:02d}"


def _in_seasons(url: str, wanted: tuple[str, ...]) -> bool:
    return any(f"/events/{s}/" in url for s in wanted)


_CAMPUS_HALLS_RE = re.compile(r"^(Zellerbach|Hertz|Wheeler|Greek Theatre|Hearst|Haas|Cal Performances)", re.I)


# Off-campus halls whose bare name the map can't place (OSM's "First Church"
# is First Church of Christ, Scientist, a different building).
_KNOWN_HALLS = {
    "first church": "First Congregational Church of Berkeley, 2345 Channing Way, Berkeley, CA 94704",
}


def location_for(hall: str) -> str:
    """Campus halls get the campus address; a hall naming its city is kept;
    any other bare name is placed in Berkeley. No hall (galas, season
    events) means Zellerbach, Cal Performances' home hall — the map can't
    place a bare "UC Berkeley". Pure."""
    if not hall:
        return f"Zellerbach Hall, {CAMPUS}"
    if hall.lower() in _KNOWN_HALLS:
        return _KNOWN_HALLS[hall.lower()]
    if "," in hall:
        return hall
    if _CAMPUS_HALLS_RE.match(hall):
        return f"{hall}, {CAMPUS}"
    return f"{hall}, Berkeley, CA"


# Titles sometimes end with the season code ("Takács Quartet … piano 2627").
_SEASON_SUFFIX_RE = re.compile(r"\s+\d{4}$")


def listing_links(html: str) -> list[str]:
    """Event pages linked from one listing page. Pure."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for a in soup.select("article.type-cp_event a.fusion-rollover-link[href]"):
        if a["href"] not in out:
            out.append(a["href"])
    return out


def _start(text: str, tz_name: str | None) -> datetime | None:
    try:
        local = datetime.strptime(re.sub(r"\s+", " ", text.strip()).lower(), "%m/%d/%Y %I:%M %p")
    except ValueError:
        return None
    try:
        tz = ZoneInfo(tz_name) if tz_name else SOURCE_TZ
    except Exception:
        tz = SOURCE_TZ
    return local.replace(tzinfo=tz).astimezone(timezone.utc)


def _meta(soup, prop: str) -> str | None:
    el = soup.select_one(f'meta[property="{prop}"]')
    return (el.get("content") or "").strip() or None if el else None


def parse_event_page(html: str, url: str) -> list[RawEvent]:
    """One event page → one RawEvent per performance. Pure."""
    soup = BeautifulSoup(html, "html.parser")
    description = _meta(soup, "og:description")
    image = _meta(soup, "og:image")
    events: list[RawEvent] = []
    seen: set[datetime] = set()
    for block in soup.select(".addeventatc"):
        get = lambda cls: (block.select_one(f".{cls}").get_text(" ", strip=True)
                           if block.select_one(f".{cls}") else "")
        start = _start(get("start"), get("timezone") or None)
        title = _SEASON_SUFFIX_RE.sub("", get("title")).strip()
        if not (start and title) or start in seen:
            continue
        seen.add(start)
        hall = get("location")
        events.append(RawEvent(
            title=title,
            start_time=start,
            location=location_for(hall),
            url=url,
            description=description,
            image_url=image,
        ))
    return events


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    wanted = seasons(datetime.now(SOURCE_TZ).date())
    links: list[str] = []
    for n in range(1, MAX_PAGES + 1):
        page = EVENTS_URL if n == 1 else f"{EVENTS_URL}page/{n}/"
        try:
            r = session.get(page, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            print(f"[calperformances] listing fetch failed ({page}): {e}", flush=True)
            break
        if r.status_code != 200:
            break
        current = [u for u in listing_links(r.text) if _in_seasons(u, wanted)]
        if not current:
            break
        links.extend(u for u in current if u not in links)

    def fetch(u: str) -> list[RawEvent]:
        try:
            r = session.get(u, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException:
            return []
        return parse_event_page(r.text, u)

    now = datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pool:
        events = [e for evs in pool.map(fetch, links) for e in evs if e.start_time >= now]
    print(f"[calperformances] {len(events)} upcoming performances from {len(links)} event pages", flush=True)
    return sorted(events, key=lambda e: e.start_time)
