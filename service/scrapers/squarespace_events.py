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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 25
DETAIL_WORKERS = 5
# Button/CTA lines to strip from a detail-page synopsis.
_CTA_RE = re.compile(r"\b(click here|buy tickets?|get tickets?|tickets|register|rsvp|learn more)\b[.!:]?", re.I)
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


def _extract_time(art) -> tuple[str | None, str]:
    """Return (time_text, strptime_format) for a card's start time.

    Squarespace themes render the start time in one of two classes: a 24-hour
    ``event-time-24hr-start`` ('19:00') or a localized 12-hour
    ``event-time-localized-start`` / ``event-time-12hr-start`` ('7:00 PM').
    """
    el = art.select_one("time.event-time-24hr-start")
    if el and el.get_text(strip=True):
        return el.get_text(strip=True), "%H:%M"
    el = (art.select_one("time.event-time-localized-start")
          or art.select_one("time.event-time-12hr-start"))
    if el and el.get_text(strip=True):
        return el.get_text(strip=True), "%I:%M %p"
    return None, ""


def _parse_start(date_iso: str | None, time_text: str | None, time_fmt: str) -> datetime | None:
    """Combine an ISO date ('2026-09-22') with a start time, SF-local → UTC.

    Falls back to midnight when a card exposes no start time."""
    if not date_iso:
        return None
    if time_text:
        stamp, fmt = f"{date_iso} {time_text}", f"%Y-%m-%d {time_fmt}"
    else:
        stamp, fmt = date_iso, "%Y-%m-%d"
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
        time_text, time_fmt = _extract_time(art)
        start_time = _parse_start(
            date_el.get("datetime") if date_el else None,
            time_text, time_fmt,
        )
        if not (title and start_time):
            continue

        link = art.select_one("a.eventlist-title-link")
        href = link.get("href") if link else None
        url = origin + href if href and href.startswith("/") else href

        # Theme variants: some cards use img.eventlist-thumbnail, others a plain
        # <img> inside the thumbnail column with only data-src.
        img = (art.select_one("img.eventlist-thumbnail")
               or art.select_one(".eventlist-column-thumbnail img")
               or art.find("img"))
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


def parse_detail_description(html: str) -> str | None:
    """Full synopsis from an event's detail page (``.eventitem-column-content``).

    The date/venue/ICS block lives in a separate ``.eventitem-column-meta``, so
    this container is just the prose; we strip trailing CTA/button lines."""
    soup = BeautifulSoup(html, "html.parser")
    el = soup.select_one(".eventitem-column-content") or soup.select_one(".eventitem-column-content .sqs-block-content")
    if not el:
        return None
    text = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
    text = _CTA_RE.sub("", text).strip(" ·-—")
    return text or None


def scrape_collection(calendar_url: str, *, fallback_location: str | None = None,
                      enrich_descriptions: bool = False) -> list[RawEvent]:
    """Fetch and parse a venue's Squarespace events-collection page.

    `enrich_descriptions` fetches each event's detail page for the full synopsis
    (``.eventitem-column-content``) — use it for venues whose collection cards
    carry thin/empty descriptions (e.g. FACT/SF). Leave it off for venues with
    rich cards (Balboa, Medicine for Nightmares) to avoid needless fetches.
    """
    try:
        resp = requests.get(calendar_url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        _log(f"fetch failed for {calendar_url}: {exc}")
        return []
    events = parse_events(resp.text, base_url=calendar_url, fallback_location=fallback_location)

    if enrich_descriptions:
        _enrich_from_detail_pages(events)
    _log(f"{calendar_url}: {len(events)} events")
    return events


def _enrich_from_detail_pages(events: list[RawEvent]) -> None:
    """Fill each event's description from its detail page, in place."""
    def fetch(ev: RawEvent) -> str | None:
        if not ev.url:
            return None
        try:
            r = requests.get(ev.url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException:
            return None
        return parse_detail_description(r.text)

    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        for ev, desc in zip(events, pool.map(fetch, events)):
            if desc:
                ev.description = desc
