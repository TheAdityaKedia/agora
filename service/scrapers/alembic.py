"""The Berkeley Alembic events — via Momence's public schedule API.

The Alembic (a West Berkeley contemplative/community center) lists events on
berkeleyalembic.org/events-2 with a client-side Momence "host-schedule" widget,
so the page HTML carries no events. The widget reads a public, unauthenticated
JSON endpoint on Momence's read-only API:

    https://readonly-api.momence.com/host-plugins/host/71603/host-schedule/sessions?page=N

Each page holds ~20 sessions (one per occurrence) with name, UTC start, room,
description (oddly, in the `level` field), image, and a momence.com/s/<id>
booking link. `pagination.totalCount` says when to stop.

Dropped: cancelled sessions, and online-only copies — hybrid talks appear
twice, the second as "<title> (ONLINE)" with `inPerson: false` and no room.
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA

SOURCE = "berkeleyalembic.org"
NAME = "The Berkeley Alembic"
HOST_ID = 71603
API_URL = f"https://readonly-api.momence.com/host-plugins/host/{HOST_ID}/host-schedule/sessions"
VENUE = "The Berkeley Alembic"
STREET = "2820 Seventh Street, Berkeley, CA"
ADDRESS = f"{VENUE}, {STREET}"
PAGE_SIZE = 20
MAX_PAGES = 50  # ~1000 sessions; a safety stop if pagination misbehaves
REQUEST_TIMEOUT = 25


def _log(msg: str) -> None:
    print(f"[alembic] {msg}", flush=True)


def matches(url: str) -> bool:
    return "berkeleyalembic.org" in url


def event_from_session(s: dict) -> RawEvent | None:
    """RawEvent for one Momence session, or None if cancelled/online/malformed."""
    if s.get("isCancelled") or not s.get("inPerson"):
        return None
    title = (s.get("sessionName") or "").strip()
    if not title or not s.get("startsAt"):
        return None
    try:
        start = datetime.fromisoformat(s["startsAt"].replace("Z", "+00:00"))
    except ValueError:
        return None
    room = (s.get("location") or "").strip()
    location = ADDRESS if not room or room == "Berkeley Alembic" else f"{VENUE} ({room}), {STREET}"
    return RawEvent(
        title=title,
        start_time=start.astimezone(timezone.utc),
        location=location,
        url=s.get("link"),
        description=(s.get("level") or "").strip() or None,
        image_url=s.get("image"),
    )


def parse_sessions(payload: dict) -> list[RawEvent]:
    return [e for e in map(event_from_session, payload.get("payload") or []) if e]


def scrape(url: str = API_URL) -> list[RawEvent]:
    events: list[RawEvent] = []
    seen = 0
    for page in range(MAX_PAGES):
        resp = requests.get(API_URL, params={"page": page, "pageSize": PAGE_SIZE},
                            headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        sessions = data.get("payload") or []
        events.extend(parse_sessions(data))
        seen += len(sessions)
        total = (data.get("pagination") or {}).get("totalCount") or 0
        if not sessions or seen >= total:
            break
    _log(f"done: {len(events)} events from {seen} sessions")
    return events
