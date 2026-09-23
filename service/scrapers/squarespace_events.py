"""Shared Squarespace "Events Collection" scraping helpers.

Many venues on Squarespace publish an Events Collection page (the built-in
event list) — a server-rendered list of ``article.eventlist-event`` cards, one
per showing, each carrying the title, an ISO event date + start time, a rich
description, a venue-hosted permalink, and a thumbnail. This is a clean,
JS-free, non-WAF source, so any such venue plugs in with a thin per-source
wrapper (see scrapers/balboa.py and scrapers/fourstar.py) supplying only its
calendar URL.

Why not the Squarespace ``?format=json`` feed: for these event collections it
paginates backward through empty past months and returns upcoming events
inconsistently, so we parse the rendered list instead — it reliably contains
every upcoming showing in one page.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 25
# Titles carry a "~ <showtime>" suffix ("Resident Evil ~ 7:30 PM"); the exact
# time is already in the structured markup, so drop the suffix from the title.
_TITLE_SUFFIX_RE = re.compile(r"\s*~\s.*$")


def _log(msg: str) -> None:
    print(f"[squarespace] {msg}", flush=True)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _clean_title(raw: str) -> str:
    return _TITLE_SUFFIX_RE.sub("", raw).strip()


def _parse_start(date_iso: str | None, time_24h: str | None) -> datetime | None:
    """Combine an ISO date ('2026-09-22') and a 24h time ('19:00'), SF-local → UTC."""
    if not date_iso:
        return None
    fmt = "%Y-%m-%d %H:%M" if time_24h else "%Y-%m-%d"
    stamp = f"{date_iso} {time_24h}" if time_24h else date_iso
    try:
        naive = datetime.strptime(stamp, fmt)
    except ValueError:
        return None
    return naive.replace(tzinfo=SOURCE_TZ).astimezone(timezone.utc)


def parse_events(html: str, *, base_url: str, fallback_location: str | None = None) -> list[RawEvent]:
    """Parse a Squarespace events-collection page into RawEvents (one per showing).

    Pure — no network — so it's testable against a captured page.
    """
    soup = BeautifulSoup(html, "html.parser")
    origin = _origin(base_url)
    events: list[RawEvent] = []
    for art in soup.select("article.eventlist-event"):
        title_el = art.select_one(".eventlist-title")
        if not title_el:
            continue
        title = _clean_title(title_el.get_text(strip=True))

        date_el = art.select_one("time.event-date")
        time_el = art.select_one("time.event-time-24hr-start")
        start_time = _parse_start(
            date_el.get("datetime") if date_el else None,
            time_el.get_text(strip=True) if time_el else None,
        )
        if not (title and start_time):
            continue

        link = art.select_one("a.eventlist-title-link")
        href = link.get("href") if link else None
        url = origin + href if href and href.startswith("/") else href

        img = art.select_one("img.eventlist-thumbnail")
        image_url = (img.get("data-src") or img.get("src")) if img else None

        desc_el = art.select_one(".eventlist-description")
        description = None
        if desc_el:
            text = desc_el.get_text(" ", strip=True)
            description = re.sub(r"\s+", " ", text).strip() or None

        events.append(RawEvent(
            title=title,
            start_time=start_time,
            location=fallback_location,
            url=url,
            description=description,
            image_url=image_url,
        ))
    return events


def scrape_collection(calendar_url: str, *, fallback_location: str | None = None) -> list[RawEvent]:
    """Fetch and parse a venue's Squarespace events-collection page."""
    try:
        resp = requests.get(calendar_url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        _log(f"fetch failed for {calendar_url}: {exc}")
        return []
    events = parse_events(resp.text, base_url=calendar_url, fallback_location=fallback_location)
    _log(f"{calendar_url}: {len(events)} events")
    return events
