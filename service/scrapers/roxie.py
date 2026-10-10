"""Roxie Theater events scraper.

The Mission's nonprofit repertory cinema (3117 16th St). WordPress; the
``/calendar/`` page renders the next three months as
``.full-calendar-block__month`` blocks titled "October 2026", each with
``.calendar-day-item`` cells (``.calendar-day`` = day of month) holding
``.film`` entries: a title link to ``/film/<slug>/`` and a
``.film-showtime`` ("6:00 pm"). One event per screening. Each film page's
``og:description`` / ``og:image`` are per film (checked 2026-10), so film
pages with an upcoming screening are fetched once each for the blurb and
still.
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


SOURCE = "roxie.com"
NAME = "Roxie Theater"
EVENTS_URL = "https://roxie.com/calendar/"
VENUE = "Roxie Theater, 3117 16th St, San Francisco, CA 94103"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 30
PAGE_WORKERS = 5


def matches(url: str) -> bool:
    return "roxie.com" in url


def parse_calendar(html: str) -> list[RawEvent]:
    """Calendar month blocks → one RawEvent per screening (no blurb yet). Pure."""
    soup = BeautifulSoup(html, "html.parser")
    events: list[RawEvent] = []
    seen: set[tuple[str, datetime]] = set()
    for block in soup.select(".full-calendar-block__month"):
        title_el = block.select_one(".calendar-block__month-title")
        try:
            month = datetime.strptime(title_el.get_text(strip=True), "%B %Y")
        except (AttributeError, ValueError):
            continue
        for cell in block.select(".calendar-day-item"):
            try:
                day = int(cell.select_one(".calendar-day").get_text(strip=True))
            except (AttributeError, ValueError):
                continue
            for film in cell.select(".film"):
                link = film.select_one("a[href]")
                name = film.select_one(".film-title")
                if not (link and name):
                    continue
                for st in film.select(".film-showtime"):
                    try:
                        t = datetime.strptime(re.sub(r"\s+", "", st.get_text()).lower(), "%I:%M%p")
                        local = month.replace(day=day, hour=t.hour, minute=t.minute, tzinfo=SOURCE_TZ)
                    except ValueError:
                        continue
                    start = local.astimezone(timezone.utc)
                    key = (link["href"], start)
                    if key in seen:
                        continue
                    seen.add(key)
                    events.append(RawEvent(title=name.get_text(" ", strip=True), start_time=start,
                                           location=VENUE, url=link["href"], description=None))
    return sorted(events, key=lambda e: e.start_time)


def parse_film_page(html: str) -> tuple[str | None, str | None]:
    """(description, image) from a film page's og tags. Pure."""
    soup = BeautifulSoup(html, "html.parser")
    def meta(prop):
        el = soup.select_one(f'meta[property="{prop}"]')
        return (el.get("content") or "").strip() or None if el else None
    return meta("og:description"), meta("og:image")


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    resp = session.get(EVENTS_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    now = datetime.now(timezone.utc)
    events = [e for e in parse_calendar(resp.text) if e.start_time >= now]

    def fetch(page: str) -> tuple[str | None, str | None]:
        try:
            r = session.get(page, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException:
            return None, None
        return parse_film_page(r.text)

    pages = sorted({e.url for e in events if e.url})
    with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pool:
        details = dict(zip(pages, pool.map(fetch, pages)))
    for e in events:
        e.description, e.image_url = details.get(e.url, (None, None))
    print(f"[roxie] {len(events)} screenings of {len(pages)} films", flush=True)
    return events
