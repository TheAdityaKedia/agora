"""Paramount Theatre (Oakland) events scraper.

The 1931 art-deco Paramount hosts the Oakland Symphony, Oakland Ballet,
Paramount Movie Classics, comedy and touring concerts. Its site runs the
Carbonhouse venue CMS: the event list pages through
``/events/events_ajax/<offset>`` (6 at a time, a JSON-encoded HTML fragment,
empty past the end), one ``.eventItem`` card per performance with a single
date ("Oct. 31 | 2026"), a start time ("Event Starts 3:00 PM"), a presenter
line, a thumbnail and a detail link. Each detail page adds the description
(``.event_description``) and a larger ``og:image``. Tickets are Ticketmaster;
we link the Paramount's own detail page.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "paramountoakland.org"
NAME = "Paramount Theatre"
BASE_URL = "https://www.paramountoakland.org"
EVENTS_URL = "https://www.paramountoakland.org/events"
AJAX_URL = BASE_URL + "/events/events_ajax/{offset}"
AJAX_PARAMS = {"category": 0, "venue": 0, "team": 0, "exclude": "", "per_page": 6,
               "came_from_page": "event-list-page"}
ADDRESS = "Paramount Theatre, 2025 Broadway, Oakland, CA 94612"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
PER_PAGE = 6
MAX_PAGES = 30
PAGE_WORKERS = 3
REQUEST_TIMEOUT = 25

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE_RE = re.compile(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})\s*\|\s*(\d{4})")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)", re.I)
# Ticketing promos that open some descriptions: "Proudly Sponsored by PG&E
# Early Bird Special available … No code required."
_PROMO_RE = re.compile(r"^\s*(?:Proudly sponsored by .*?(?=Early Bird)\s*)?"
                       r"(?:Early Bird Special.*?No code required\.\s*)?", re.I | re.S)


def matches(url: str) -> bool:
    return "paramountoakland.org" in url


def _text(el) -> str:
    return " ".join(el.get_text(" ").split()) if el else ""


def parse_start(date_text: str, time_text: str) -> datetime | None:
    """"Oct. 31 | 2026" + "Event Starts 3:00 PM" → UTC. No time → 8 PM. Pure."""
    m = _DATE_RE.search(date_text or "")
    if not m or m.group(1).lower() not in _MONTHS:
        return None
    hour, minute = 20, 0
    t = _TIME_RE.search(time_text or "")
    if t:
        hour, minute = int(t.group(1)) % 12 + (12 if t.group(3).upper() == "PM" else 0), int(t.group(2))
    local = datetime(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)), hour, minute, tzinfo=SOURCE_TZ)
    return local.astimezone(ZoneInfo("UTC"))


def parse_list(fragment: str) -> list[RawEvent]:
    """One page of ``.eventItem`` cards → RawEvents (no description yet). Pure."""
    events = []
    for item in BeautifulSoup(fragment, "html.parser").select(".eventItem"):
        link = item.select_one(".title a[href]")
        start = parse_start(_text(item.select_one(".date")), _text(item.select_one(".time")))
        if not (link and start):
            continue
        img = item.select_one(".thumb img[src]")
        events.append(RawEvent(
            title=_text(link),
            start_time=start,
            location=ADDRESS,
            url=link["href"],
            description=_text(item.select_one(".presented-by")) or None,
            image_url=img["src"] if img else None,
        ))
    return events


def parse_detail(html: str) -> tuple[str | None, str | None]:
    """A detail page's (description, og:image). Pure."""
    soup = BeautifulSoup(html, "html.parser")
    desc = _PROMO_RE.sub("", _text(soup.select_one(".event_description"))).strip() or None
    og = soup.select_one('meta[property="og:image"]')
    return desc, (og.get("content") or None) if og else None


def _fetch_detail(session: requests.Session, url: str) -> tuple[str | None, str | None]:
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException:
        return None, None
    return parse_detail(r.text)


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA, "X-Requested-With": "XMLHttpRequest"})
    events: list[RawEvent] = []
    for page in range(MAX_PAGES):
        r = session.get(AJAX_URL.format(offset=page * PER_PAGE), params=AJAX_PARAMS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        try:
            fragment = json.loads(r.text)
        except ValueError:
            fragment = r.text
        batch = parse_list(fragment if isinstance(fragment, str) else "")
        if not batch:
            break
        events.extend(batch)
    pages = sorted({e.url for e in events})
    with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pool:
        details = dict(zip(pages, pool.map(lambda u: _fetch_detail(session, u), pages)))
    for e in events:
        desc, image = details.get(e.url, (None, None))
        if desc:
            e.description = f"{e.description}. {desc}" if e.description else desc
        e.image_url = image or e.image_url
    print(f"[paramount] {len(events)} events", flush=True)
    return events
