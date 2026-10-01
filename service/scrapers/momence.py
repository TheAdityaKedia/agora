"""Momence (momence.com) — single-session lookup for submitted links.

Many studios sell classes and events through Momence. A session page
(`momence.com/s/<id>`, or the long `momence.com/<host>/<name>/<id>` form it
redirects to) is a JavaScript shell behind Cloudflare with no data in its
HTML, but it loads everything from a public, unauthenticated JSON endpoint:

    https://momence.com/_api/readonly/plugin/sessions/<id>

→ `{"status": "success", "message": {...session...}}` with the name, UTC
start, room (`location`), in-person/cancelled flags, the description (in the
`level` field, as in the host-schedule API scrapers/alembic.py uses), images,
and `locationEntity` whose (parent) location carries the physical address.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from scrapers.base import RawEvent

SESSION_API = "https://momence.com/_api/readonly/plugin/sessions/{id}"
SHORT_URL = "https://momence.com/s/{id}"
_SESSION_URL_RE = re.compile(r"^https?://(?:www\.)?momence\.com/(?:s/(\d+)|[^/?#]+/[^/?#]+/(\d+))(?:[/?#]|$)")


def session_id(url: str) -> str | None:
    """The session id in a Momence session link, or None if it isn't one."""
    m = _SESSION_URL_RE.match(url or "")
    return (m.group(1) or m.group(2)) if m else None


def session_api_url(sid: str) -> str:
    return SESSION_API.format(id=sid)


def _address(entity: dict | None) -> str | None:
    """Physical address from a location entity or its parent (rooms have none)."""
    for loc in (entity or {}, (entity or {}).get("parentLocation") or {}):
        if loc.get("isPhysical") and (loc.get("fullAddress") or "").strip():
            return loc["fullAddress"].strip()
    return None


def _venue(entity: dict | None, room: str) -> str | None:
    parent = ((entity or {}).get("parentLocation") or {}).get("location") or ""
    if parent and room and room != parent:
        return f"{parent} ({room})"
    return parent or room or None


def event_from_session_detail(s: dict) -> RawEvent | None:
    """RawEvent for one session payload, or None if cancelled/online/draft/malformed."""
    if s.get("isCancelled") or s.get("isDraft") or not s.get("inPerson"):
        return None
    title = (s.get("sessionName") or "").strip()
    if not title or not s.get("startsAt"):
        return None
    try:
        start = datetime.fromisoformat(s["startsAt"].replace("Z", "+00:00"))
    except ValueError:
        return None
    entity = s.get("locationEntity")
    venue = _venue(entity, (s.get("location") or "").strip())
    address = _address(entity)
    location = ", ".join(p for p in (venue, address) if p) or None
    description = (s.get("level") or s.get("description") or "").strip() or None
    return RawEvent(
        title=title,
        start_time=start.astimezone(timezone.utc),
        location=location,
        url=SHORT_URL.format(id=s["id"]) if s.get("id") else None,
        description=description,
        image_url=s.get("topImage1") or None,
    )
