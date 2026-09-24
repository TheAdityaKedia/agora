"""Shared helpers for the Elfsight Event Calendar widget.

Sites that embed Elfsight's Event Calendar (a common no-code widget) load their
events from ``https://widget-data.service.elfsight.com/api/events?source=<id>``
— a clean JSON API. Each event has a tz-aware ISO ``start.dateTime``, an HTML
``description`` (wrapped in a ``<html-blob>``), a ``venue`` address, and an
optional image/button link. Any such venue plugs in with a thin per-source
wrapper (see scrapers/riptide.py) supplying its widget source id.

The API returns up to 100 events from a ``from`` date, so we walk forward by
date windows, but bound the walk to a near-term horizon: venues here tend to be
recurring nights (trivia, karaoke, open mic), and projecting a full year of
weekly repeats would flood the manifest.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


API_URL = "https://widget-data.service.elfsight.com/api/events"
REQUEST_TIMEOUT = 25
DEFAULT_HORIZON_DAYS = 90
MAX_WINDOWS = 6  # safety bound on pagination


def _log(msg: str) -> None:
    print(f"[elfsight] {msg}", flush=True)


def _clean_html(raw: str | None) -> str | None:
    if not raw:
        return None
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _parse_start(start: dict | None) -> datetime | None:
    """Parse an event's start block (tz-aware dateTime, or an all-day date)."""
    if not isinstance(start, dict):
        return None
    dt = start.get("dateTime")
    if dt:
        try:
            return datetime.fromisoformat(dt).astimezone(timezone.utc)
        except ValueError:
            return None
    date = start.get("date")
    if date:
        try:
            # All-day: treat as UTC midnight (rare for these venues).
            return datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def parse_events(payload: list[dict], *, fallback_location: str | None = None,
                 fallback_url: str | None = None) -> list[RawEvent]:
    """Map Elfsight event dicts to RawEvents. Pure — testable against a payload."""
    events: list[RawEvent] = []
    for e in payload or []:
        name = (e.get("name") or "").strip()
        start_time = _parse_start(e.get("start"))
        if not (name and start_time):
            continue
        venue = e.get("venue") or {}
        location = (venue.get("name") or venue.get("address") or "").strip() or fallback_location
        image = e.get("image")
        if isinstance(image, dict):
            image = image.get("url")
        events.append(RawEvent(
            title=name,
            start_time=start_time,
            location=location,
            url=e.get("buttonLink") or fallback_url,
            description=_clean_html(e.get("description")),
            image_url=image if isinstance(image, str) else None,
        ))
    return events


def scrape_events(source_id: str, *, fallback_location: str | None = None,
                  fallback_url: str | None = None,
                  horizon_days: int = DEFAULT_HORIZON_DAYS) -> list[RawEvent]:
    """Fetch upcoming events for an Elfsight widget source, bounded to a horizon.

    Walks forward by date windows (the API caps each response at 100), deduping
    by event id, until events pass the horizon or the window bound is hit.
    """
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=horizon_days)
    headers = {"User-Agent": BROWSER_UA, "Accept": "application/json"}
    from_date = now.date()
    seen: set[str] = set()
    collected: list[RawEvent] = []

    for _ in range(MAX_WINDOWS):
        params = {"source": source_id, "from": from_date.isoformat()}
        try:
            resp = requests.get(API_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json().get("payload", [])
        except (requests.RequestException, ValueError) as exc:
            _log(f"fetch failed for {source_id} from {from_date}: {exc}")
            break
        if not payload:
            break

        last_start = None
        new = 0
        for raw, ev in zip(payload, parse_events(payload, fallback_location=fallback_location,
                                                 fallback_url=fallback_url)):
            last_start = ev.start_time
            eid = raw.get("id")
            if eid in seen:
                continue
            seen.add(eid)
            if ev.start_time <= horizon:
                collected.append(ev)
                new += 1

        # Advance the window past the last event; stop once we're beyond horizon.
        if last_start is None or last_start > horizon or len(payload) < 100:
            break
        from_date = (last_start.astimezone(timezone.utc) + timedelta(days=1)).date()

    _log(f"{source_id}: {len(collected)} events within {horizon_days}d")
    return collected
