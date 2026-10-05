"""Event lifecycle: updates, seen-tracking and statuses
(feature-specs/event-lifecycle.md, §2–3).

Statuses on a row:
    scheduled  the default; listed and on
    cancelled  the creating source says so (a flag, or a "CANCELLED:" title)
    postponed  likewise
    unlisted   every source that listed it stopped, two good scrapes running
    moved      unlisted, and its source now lists the same URL at a new time
               (the new row carries changed.start_time; an alias links them)
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime

SCHEDULED, CANCELLED, POSTPONED, UNLISTED, MOVED = (
    "scheduled", "cancelled", "postponed", "unlisted", "moved")
EXPLICIT = (CANCELLED, POSTPONED)        # what a scraper can say
LISTED = (SCHEDULED, CANCELLED, POSTPONED)  # what the manifest shows
GONE = (UNLISTED, MOVED)

# The fields the creating source owns; a change to any is recorded.
TRACKED_FIELDS = ("title", "location", "url", "description", "image_url")

# "CANCELLED: Jazz Night", "[Canceled] Jazz Night", "POSTPONED - Jazz Night",
# "(Cancelled) …"; and at the end, "Jazz Night (Cancelled)", "Jazz Night -
# POSTPONED". A bare leading word needs a separator after it, so a show
# called "Cancelled Plans" stays a show.
_W = r"(?P<word>cancel+ed|postponed)"
_MARKERS = [re.compile(p, re.IGNORECASE) for p in (
    rf"^\s*[\[(]\s*{_W}\s*[\])]\s*[:\-–—|]?\s*(?P<rest>.+)$",  # [Canceled] Jazz Night
    rf"^\s*{_W}\s*[:\-–—|!]+\s*(?P<rest>.+)$",                    # CANCELLED: Jazz Night
    rf"^(?P<rest>.+?)\s*[\[(]\s*{_W}\s*[\])]\s*$",               # Jazz Night (Cancelled)
    rf"^(?P<rest>.+?)\s+[\-–—|:]\s*{_W}!*\s*$",                    # Jazz Night - POSTPONED
)]


def title_status(title: str) -> tuple[str, str | None]:
    """(title without the marker, "cancelled"/"postponed" or None)."""
    for rx in _MARKERS:
        m = rx.match(title or "")
        if m:
            status = POSTPONED if m.group("word").lower() == "postponed" else CANCELLED
            return m.group("rest").strip(), status
    return title, None


def with_title_status(raw):
    """The RawEvent with a status marker moved from its title to `status`
    (so its id and matching don't change), or `raw` unchanged."""
    title, status = title_status(raw.title)
    if status is None:
        return raw
    return replace(raw, title=title, status=raw.status or status)


def _iso(t: datetime) -> str:
    return t.isoformat()


def mark_seen(row, source: str, now: datetime) -> None:
    """The source listed this row in a good scrape: seen now, no misses, and
    an unlisted (or moved) row is back: it was a flaky scrape."""
    row.seen = {**(row.seen or {}), source: _iso(now)}  # reassign: JSON isn't tracked in place
    row.misses = {**(row.misses or {}), source: 0}
    if row.status in GONE:
        set_status(row, SCHEDULED, now)


def set_status(row, status: str, now: datetime) -> bool:
    if (row.status or SCHEDULED) == status:
        return False
    row.status, row.status_at = status, now
    return True


def _same(a, b) -> bool:
    return (a or None) == (b or None)  # "" and None are both "nothing"


def apply_update(row, raw, now: datetime) -> bool:
    """The creating source re-listed this row: take its current fields and
    status. Returns whether anything changed. Field changes are recorded in
    `changed` / `changed_at`; a status change in `status_at`."""
    was = {f: getattr(row, f) for f in TRACKED_FIELDS if not _same(getattr(row, f), getattr(raw, f))}
    for f in was:
        setattr(row, f, getattr(raw, f))
    if was:
        row.changed, row.changed_at = was, now
    # An explicit status applies at once; listed again without one, it's on.
    flipped = False
    if raw.status in EXPLICIT:
        flipped = set_status(row, raw.status, now)
    elif row.status in EXPLICIT:
        flipped = set_status(row, SCHEDULED, now)
    return bool(was) or flipped
