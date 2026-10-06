"""Shared Tugoz (ticketing) helpers.

Tugoz sells tickets through a widget (`tugoz.com/js/tugoz.js`) that venues embed
with an event id. The widget reads a public, static JSON feed per event:

    https://static.tugoz.com/api/json/www/v4/e-<eventId>

`einfo` describes that event, and `einfo.related` lists every show in its
series (each its own Tugoz event id), with a UTC start (`utcdate`), venue +
street address, and `status`. Tugoz has no host-level listing, so a wrapper
has to know at least one event id per series (e.g. from the venue's embed
config) and expands it into shows here.

The feed is CDN-cached, so its `ispast` flags can be stale: we decide what's
upcoming from `utcdate` ourselves.
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from scrapers.browser import BROWSER_UA

FEED_URL = "https://static.tugoz.com/api/json/www/v4/e-{event_id}"
REQUEST_TIMEOUT = 25


def fetch_feed(event_id: int | str) -> dict:
    resp = requests.get(FEED_URL.format(event_id=event_id), headers={"User-Agent": BROWSER_UA},
                        timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def shows(feed: dict) -> list[dict]:
    """Every show in the feed's series (the event itself if it has no series)."""
    info = feed.get("einfo") or {}
    by_id: dict = {}
    for show in info.get("related") or [info]:
        if show.get("eventid") is not None:
            by_id.setdefault(show["eventid"], show)
    return list(by_id.values())


def show_start(show: dict) -> datetime | None:
    try:
        return datetime.strptime(show["utcdate"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def upcoming_shows(feed: dict, now: datetime | None = None) -> list[dict]:
    """Active shows starting at or after `now`, in start order."""
    now = now or datetime.now(timezone.utc)
    upcoming = [s for s in shows(feed)
                if s.get("status") == "Active" and (show_start(s) or now) > now]
    return sorted(upcoming, key=show_start)


def show_title(show: dict) -> str:
    """Series name plus the show's label when it adds something
    ("Laugh Ticket 10 — Danville"; "Open Mic" stays as is)."""
    name = (show.get("name") or "").strip()
    label = (show.get("shortname") or "").strip()
    return f"{name} — {label}" if label and label.lower() not in name.lower() else name


def show_location(show: dict) -> str | None:
    venue = (show.get("venue") or show.get("vname") or "").strip()
    region = " ".join(p for p in (show.get("state"), show.get("zip")) if p)
    parts = [venue, (show.get("address1") or "").strip(), (show.get("city") or "").strip(), region]
    return ", ".join(p for p in parts if p) or None
