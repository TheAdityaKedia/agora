"""Stable event ids and aliases (feature-specs/event-lifecycle.md, §1).

A new row's id is uuid5(AGORA_NS, key), where the key is the creating
source (the row's first source) plus the event's identity in that source:

    url present:  "<source>|url|<url>|<start_time as UTC ISO, seconds>"
    no url:       "<source>|title|<normalized title>|<start_time as UTC ISO>"

So a wipe and re-scrape gives the same ids. A new start time is a new id (a
rescheduled performance is a different instance); when an id does change
(duplicates merged, rescheduled, the one-off re-key) the old id gets a row
in `event_aliases` pointing at the new one, and `resolve` follows the chain.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timezone

# Fixed forever: changing it would change every id.
AGORA_NS = uuid.UUID("5b0f6a8e-3c1d-4e2a-9f47-6d2b8c1e0a93")

ALIAS_REASONS = ("merged", "rekeyed", "moved")


def normalize_title(title: str | None) -> str:
    """Casefold, collapse whitespace, strip punctuation at the ends.

    Not dedup's normalizer: that one drops non-ASCII letters (fine for
    fuzzy matching, but it would give every all-Chinese title the same key).
    """
    t = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", title or "")).casefold()

    def edge(c):  # whitespace, punctuation or a symbol
        return c.isspace() or unicodedata.category(c)[0] in "PS"
    i, j = 0, len(t)
    while i < j and edge(t[i]):
        i += 1
    while j > i and edge(t[j - 1]):
        j -= 1
    return t[i:j]


def _utc_iso(start: datetime) -> str:
    if start.tzinfo is None:  # SQLite hands back naive datetimes (stored as UTC)
        start = start.replace(tzinfo=timezone.utc)
    return start.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def event_key(source: str, url: str | None, title: str | None, start: datetime) -> str:
    if url:
        return f"{source}|url|{url}|{_utc_iso(start)}"
    return f"{source}|title|{normalize_title(title)}|{_utc_iso(start)}"


def event_id(source: str, url: str | None, title: str | None, start: datetime) -> uuid.UUID:
    return uuid.uuid5(AGORA_NS, event_key(source, url, title, start))


def id_of_row(event) -> uuid.UUID | None:
    """The id an Event row should have (its creating source's key), or None
    for a row with no source."""
    sources = event.sources or []
    if not sources:
        return None
    return event_id(sources[0], event.url, event.title, event.start_time)


def resolve(aliases: dict, event_id_: str, live: set | None = None) -> str:
    """Follow old → new through `aliases` ({old: new}) to the current id.

    A live id (a row that exists) is its own answer even if an old alias
    names it. Cycles stop at the last id before repeating.
    """
    seen = set()
    cur = event_id_
    while cur in aliases and cur not in seen and not (live is not None and cur in live):
        seen.add(cur)
        cur = aliases[cur]
    return cur


def add_alias(session, old_id, new_id, reason: str, at: datetime | None = None) -> None:
    """Record old → new (replacing an older alias of the same old id)."""
    from models import EventAlias

    if reason not in ALIAS_REASONS:
        raise ValueError(f"unknown alias reason {reason!r}")
    if old_id == new_id:
        return
    at = at or datetime.now(timezone.utc)
    row = session.get(EventAlias, old_id)
    if row is None:
        session.add(EventAlias(old_id=old_id, new_id=new_id, reason=reason, at=at))
    else:
        row.new_id, row.reason, row.at = new_id, reason, at


def alias_map(session) -> dict[str, str]:
    """Every alias as {old id: new id} (strings), unresolved."""
    from models import EventAlias

    return {str(a.old_id): str(a.new_id) for a in session.query(EventAlias).all()}
