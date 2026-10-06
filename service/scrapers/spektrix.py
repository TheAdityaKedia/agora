"""Shared helpers for venues ticketing through Spektrix.

Spektrix's public read API (v3) needs no browser or key — just the venue's
ticketing base, visible in its pages as ``spektrix_base`` or links to
``<host>/<client>/website/…``:

- ``/api/v3/events`` — the catalog: name, plain-text description, image,
  and the venue's own ``webUrl`` for the show.
- ``/api/v3/instances?startFrom=YYYY-MM-DD`` — one row per performance with
  ``startUtc``, ``cancelled`` and the event id.

parse_events joins them into one RawEvent per non-cancelled performance. The
API carries no venue, so wrappers pass a fallback location. Reference wrapper:
scrapers/stanfordlive.py.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

import requests

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


REQUEST_TIMEOUT = 30


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_events(events: list[dict], instances: list[dict], *,
                 fallback_location: str) -> list[RawEvent]:
    """Join performances with the event catalog. Pure."""
    catalog = {e.get("id"): e for e in events or [] if isinstance(e, dict)}
    out: list[RawEvent] = []
    for inst in instances or []:
        if inst.get("cancelled"):
            continue
        ev = catalog.get((inst.get("event") or {}).get("id"))
        start = _parse_utc(inst.get("startUtc"))
        if not ev or not start:
            continue
        title = (ev.get("name") or "").strip()
        if not title:
            continue
        desc = re.sub(r"\s+", " ", ev.get("description") or "").strip()
        out.append(RawEvent(
            title=title,
            start_time=start,
            location=fallback_location,
            url=ev.get("webUrl") or None,
            description=desc or None,
            image_url=ev.get("imageUrl") or None,
        ))
    return sorted(out, key=lambda e: e.start_time)


def scrape(api_base: str, *, fallback_location: str, tag: str = "spektrix") -> list[RawEvent]:
    """`api_base` is ``https://<host>/<client>/api/v3``."""
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA, "Accept": "application/json"})
    try:
        events = session.get(f"{api_base}/events", timeout=REQUEST_TIMEOUT)
        events.raise_for_status()
        instances = session.get(f"{api_base}/instances", params={"startFrom": date.today().isoformat()},
                                timeout=REQUEST_TIMEOUT)
        instances.raise_for_status()
        out = parse_events(events.json(), instances.json(), fallback_location=fallback_location)
    except (requests.RequestException, ValueError) as e:
        print(f"[{tag}] API fetch failed: {e}", flush=True)
        return []
    print(f"[{tag}] {len(out)} performances", flush=True)
    return out
