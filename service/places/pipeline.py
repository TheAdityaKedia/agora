"""Venue resolution as a pipeline step (feature-specs/venues.md, phase 2).

The merge job calls `main.resolve_places()` after tagging and before export;
it gathers upcoming events' location strings and hands them here. Resolution
never blocks the export: a malformed venue file skips the step (and is
reported), and the lookup cap rolls the rest to the next run.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from .normalize import normalize_key
from .resolve import Resolver
from .store import Store

# Nominatim lookups per run (≈1 s each); the rest roll to the next run.
MAX_LOOKUPS = 50


def collect_locations(events) -> dict[str, dict]:
    """(location, sources) pairs or event dicts → per normalised key:
    {"text": most common raw form, "events": n, "sources": [...]}, plus
    "example" — the soonest event's title/url/date — when the dicts carry
    "title" and "start" (an ISO date), so the review issue can name it."""
    raw: dict[str, Counter] = defaultdict(Counter)
    sources: dict[str, set] = defaultdict(set)
    examples: dict[str, dict] = {}
    for e in events:
        loc, srcs = (e.get("location"), e.get("sources")) if isinstance(e, dict) else e
        loc = (loc or "").strip()
        key = normalize_key(loc)
        if not key:
            continue
        raw[key][loc] += 1
        sources[key].update(srcs or [])
        if isinstance(e, dict) and e.get("title") and e.get("start"):
            ex = {"title": e["title"], "url": e.get("url"), "date": e["start"]}
            if key not in examples or ex["date"] < examples[key]["date"]:
                examples[key] = ex
    return {k: {"text": c.most_common(1)[0][0], "events": sum(c.values()),
                "sources": sorted(sources[k]),
                **({"example": examples[k]} if k in examples else {})} for k, c in raw.items()}


def resolve_locations(locations: dict[str, dict], store: Store, geocoder,
                      today: date | None = None, assistant=None) -> dict:
    """Resolve every string not yet known (and pending ones due a retry).

    Mutates `store` (the caller saves it). Returns a summary for the run
    report: what happened, what's new, and how many upcoming events still
    have an unresolved location (unknown or pending — deliberate
    "none"/"outside" entries count as resolved).
    """
    errors = store.validate()
    if errors:
        return {"invalid": errors}
    resolver = Resolver(store, geocoder, today=today, assistant=assistant)
    pending_before = {k for k, e in store.locations.items() if "pending" in e}
    venues_before = set(store.venues)
    actions = Counter()
    new_pending = []
    for key, info in sorted(locations.items(), key=lambda kv: -kv[1]["events"]):
        out = resolver.resolve(info["text"], info["sources"], info["events"],
                               example=info.get("example"))
        actions[out.action] += 1
        if out.action == "pending" and key not in pending_before:
            new_pending.append(key)

    def unresolved(key):
        e = store.locations.get(key)
        return e is None or "pending" in e

    return {
        "actions": dict(actions),
        "lookups": getattr(geocoder, "calls", 0),
        "ai_calls": getattr(assistant, "calls", 0),
        "new_venues": sorted(set(store.venues) - venues_before),
        "new_pending": new_pending,
        "pending": sorted(k for k in locations if "pending" in store.locations.get(k, {})),
        "events_with_location": sum(i["events"] for i in locations.values()),
        "unresolved_events": sum(i["events"] for k, i in locations.items() if unresolved(k)),
    }
