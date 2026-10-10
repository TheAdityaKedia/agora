"""The UC Theatre (Berkeley) events scraper.

Downtown Berkeley's 1,400-capacity nonprofit music hall (2036 University
Ave). A Webflow site with Opendate ticketing. The homepage's
``.shows-collection-item`` cards list every announced show with genre,
headliner, openers and a poster (CSS ``background-image``), but only
"Oct / 06 / Tue" for a date. Each ``/shows/<slug>`` page has the full line
("October 6, 2026 Doors: 7:00 pm • Start: 8:00 pm"), so each show page is
fetched once for its start. Shows marked "MOVED TO <other venue>" in the
title are dropped (they're no longer here). There's no blurb anywhere on the site (checked
2026-10): descriptions are the openers and genre.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "theuctheatre.org"
NAME = "The UC Theatre"
BASE_URL = "https://www.theuctheatre.org"
EVENTS_URL = "https://www.theuctheatre.org/"
VENUE = "The UC Theatre, 2036 University Ave, Berkeley, CA 94704"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 30
PAGE_WORKERS = 5

_WHEN_RE = re.compile(
    r"\b([A-Z][a-z]+ \d{1,2}, \d{4})\b(?:.{0,40}?\bStart:\s*(\d{1,2}:\d{2}\s*[ap]m))?"
    r"(?:.{0,40}?\bDoors:\s*(\d{1,2}:\d{2}\s*[ap]m))?", re.S)
_MOVED_RE = re.compile(r"\bmoved to\b", re.I)
_BG_RE = re.compile(r"url\(['\"]?([^'\")]+)")


def matches(url: str) -> bool:
    return "theuctheatre.org" in url


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def parse_cards(html: str) -> list[dict]:
    """Homepage cards → {url, title, description, image_url}. Pure."""
    soup = BeautifulSoup(html, "html.parser")
    cards: list[dict] = []
    for item in soup.select(".shows-collection-item"):
        link = item.select_one("a[href^='/shows/']")
        title = _text(item.select_one(".headliner-listing"))
        if not (link and title) or _MOVED_RE.search(title):
            continue
        support = _text(item.select_one(".support-listing"))
        genre = _text(item.select_one(".genre"))
        bg = _BG_RE.search(link.get("style") or "")
        cards.append({
            "url": urljoin(BASE_URL, link["href"]),
            "title": title,
            "description": " · ".join(p for p in (f"with {support}" if support else "", genre) if p) or None,
            "image_url": bg.group(1) if bg else None,
        })
    return cards


def parse_start(html: str) -> datetime | None:
    """Start from a show page's "October 6, 2026 Doors: 7:00 pm • Start: 8:00 pm". Pure."""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style"]):
        t.decompose()
    text = _text(soup)
    for m in _WHEN_RE.finditer(text):
        clock = m.group(2) or m.group(3)
        try:
            day = datetime.strptime(m.group(1), "%B %d, %Y")
        except ValueError:
            continue
        hour, minute = 20, 0  # no time listed: an 8pm show
        if clock:
            t = datetime.strptime(re.sub(r"\s+", "", clock).lower(), "%I:%M%p")
            hour, minute = t.hour, t.minute
        return day.replace(hour=hour, minute=minute, tzinfo=SOURCE_TZ).astimezone(timezone.utc)
    return None


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    resp = session.get(EVENTS_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    cards = parse_cards(resp.text)

    def fetch(card: dict) -> RawEvent | None:
        try:
            r = session.get(card["url"], timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException:
            return None
        start = parse_start(r.text)
        if not start:
            return None
        return RawEvent(title=card["title"], start_time=start, location=VENUE, url=card["url"],
                        description=card["description"], image_url=card["image_url"])

    with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pool:
        events = [e for e in pool.map(fetch, cards) if e]
    print(f"[uctheatre] {len(events)} shows from {len(cards)} cards", flush=True)
    return sorted(events, key=lambda e: e.start_time)
