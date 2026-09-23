"""Shared helpers for "The Events Calendar" (Tribe) WordPress plugin.

Many WordPress venues run the ubiquitous The Events Calendar plugin, which
exposes a clean read REST API at ``/wp-json/tribe/events/v1/events``. It
defaults to upcoming events and paginates via ``next_rest_url``; each event
carries an HTML-entity-encoded title, a UTC start time, a permalink URL, an
HTML description, a venue block, and an image. Any such venue plugs in with a
thin per-source wrapper (see scrapers/birdbeckett.py) supplying its site base.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


REQUEST_TIMEOUT = 25
PER_PAGE = 50
MAX_PAGES = 40  # safety bound (~2000 events)


def _log(msg: str) -> None:
    print(f"[tribe] {msg}", flush=True)


def _api_url(site_base: str) -> str:
    return f"{site_base.rstrip('/')}/wp-json/tribe/events/v1/events"


def _clean_html(raw: str | None) -> str | None:
    if not raw:
        return None
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _venue_location(venue: dict | None) -> str | None:
    if not isinstance(venue, dict):
        return None
    parts = [html.unescape(venue.get(k) or "").strip() for k in ("venue", "address", "city")]
    parts = [p for p in parts if p]
    return ", ".join(parts) or None


def _parse_utc(value: str | None) -> datetime | None:
    """Parse a Tribe 'YYYY-MM-DD HH:MM:SS' UTC timestamp into a tz-aware datetime."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_events(events: list[dict], *, fallback_location: str | None = None) -> list[RawEvent]:
    """Map Tribe event dicts to RawEvents. Pure — testable against a captured page."""
    out: list[RawEvent] = []
    for e in events or []:
        title = html.unescape((e.get("title") or "").strip())
        start_time = _parse_utc(e.get("utc_start_date"))
        if not (title and start_time):
            continue
        out.append(RawEvent(
            title=title,
            start_time=start_time,
            location=_venue_location(e.get("venue")) or fallback_location,
            url=e.get("url") or None,
            description=_clean_html(e.get("description")),
            image_url=(e.get("image") or {}).get("url") if isinstance(e.get("image"), dict) else None,
        ))
    return out


def scrape_events(site_base: str, *, fallback_location: str | None = None) -> list[RawEvent]:
    """Fetch all upcoming events from a Tribe API, following pagination."""
    url = _api_url(site_base)
    params = {"per_page": PER_PAGE}
    events: list[RawEvent] = []
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA, "Accept": "application/json"})
    for _ in range(MAX_PAGES):
        try:
            resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            _log(f"fetch failed for {url}: {exc}")
            break
        events.extend(parse_events(data.get("events", []), fallback_location=fallback_location))
        next_url = data.get("next_rest_url")
        if not next_url:
            break
        url, params = next_url, None  # next_rest_url already carries all query params
    _log(f"{site_base}: {len(events)} events")
    return events
