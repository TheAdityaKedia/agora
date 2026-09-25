"""Fabulosa Books events scraper.

Fabulosa (a queer-focused Castro bookstore) hand-edits its events page on
Weebly: each event is a two-column block (cover image | an ``h2`` heading plus
a ``.paragraph`` blurb). The heading's lines are the title, an optional
subtitle, the author, and a date line like "Tuesday, September 29th at 7pm"
(no year; resolved by weekday via scrapers/datetext.py). No per-event pages
exist, so every event links to the events page (start_time keeps them
distinct). DOM scraping is the only option here: no JSON-LD, feed, or API.
"""
import re
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA
from scrapers.datetext import SF_TZ, parse_weekday_date


SOURCE = "fabulosabooks.com"
NAME = "Fabulosa Books"
EVENTS_URL = "https://www.fabulosabooks.com/events.html"
ADDRESS = "Fabulosa Books, 489 Castro St, San Francisco, CA 94114"
REQUEST_TIMEOUT = 25


def matches(url: str) -> bool:
    return "fabulosabooks.com" in url


def _heading_lines(h2) -> list[str]:
    for br in h2.find_all("br"):
        br.replace_with("\n")
    return [re.sub(r"\s+", " ", l).strip() for l in h2.get_text().split("\n") if l.strip()]


def parse_events(html: str, today: date) -> list[RawEvent]:
    """Events page HTML -> RawEvents. Pure."""
    soup = BeautifulSoup(html, "html.parser")
    events: list[RawEvent] = []
    for block in soup.select("table.wsite-multicol-table"):
        h2 = block.select_one("h2.wsite-content-title")
        if h2 is None:
            continue
        lines = _heading_lines(h2)
        if len(lines) < 2:
            continue
        start_time = parse_weekday_date(lines[-1], today)
        if start_time is None:
            continue
        title, rest = lines[0], lines[1:-1]
        author = rest[-1] if rest else None
        subtitle = " · ".join(rest[:-1]) or None
        blurb = " ".join(p.get_text(" ", strip=True) for p in block.select(".paragraph"))
        description = " · ".join(x for x in (subtitle, re.sub(r"\s+", " ", blurb).strip()) if x) or None
        img = block.find("img", src=True)
        events.append(RawEvent(
            title=f"{title} — {author}" if author else title,
            start_time=start_time,
            location=ADDRESS,
            url=EVENTS_URL,
            description=description,
            image_url=urljoin(EVENTS_URL, img["src"]) if img else None,
        ))
    return events


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(EVENTS_URL, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    events = parse_events(resp.text, datetime.now(SF_TZ).date())
    print(f"[fabulosa] {len(events)} events", flush=True)
    return events
