"""JSON exporter for the static-site path.

Reads persisted events from the database and writes the manifest for the
static frontend (GitHub Pages). Prunes past events so the files stay small.

Two files (feature-specs/frontend-payload.md):
    events.json        {"generated_at", "taxonomy", "regions", "venues",
                        "events": [ {...event, "summary", "more"?}, ... ]}
    descriptions.json  {"<event id>": "<full description>", ...}

Events carry the description's first sentence as `summary` (what a row shows
collapsed) and `more: true` when there is more; the full text lives in
descriptions.json, which the page fetches only on "more" or search.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import taxonomy
from classifications import Cache
from places.regions import REGIONS
from places.store import DATA_DIR as VENUES_DIR, Store as VenueStore
from db import get_session
from models import Event


# Interpret the past-window in CALENDAR DAYS in the source region, not rolling
# 24-hour periods. This is Agora's audience region (SF Bay Area), so we anchor
# to Pacific local time — events that happened earlier today (local) stay in
# the manifest until midnight PT, not until 24h after they started.
EXPORT_TZ = ZoneInfo("America/Los_Angeles")

DEFAULT_BACK_WINDOW_DAYS = 1
DEFAULT_CLASSIFICATIONS = Path(__file__).parent.parent / "data" / "classifications.json"

# The one type path that means "this event IS just food & drink" — happy hours,
# lunch specials, tastings. Events with this as their SOLE format are off-target
# for the aggregator and dropped at export (see _is_food_drink_only). Keying on
# the type (not the topic) keeps genuine events that merely touch food/drink in
# another format: a cooking WORKSHOP, a food TALK, a dinner + PERFORMANCE.
_FOOD_DRINK_TYPE = ["social", "food-drink"]


# Must match the page's PREVIEW_MAX / firstSentence() in frontend/index.html:
# the summary is exactly what a collapsed row shows.
SUMMARY_MAX = 180
DESCRIPTIONS_FILE = "descriptions.json"


def summarize(text: str | None) -> tuple[str, bool]:
    """(first sentence, whether the full text is longer) — a port of the
    page's firstSentence(): whitespace collapsed, up to the first . ! or ?
    followed by a space or the end, cut to SUMMARY_MAX at a word boundary."""
    full = re.sub(r"\s+", " ", text or "").strip()
    if not full:
        return "", False
    m = re.match(r"^[^.!?]*[.!?](?=\s|$)", full)
    s = m.group(0).strip() if m else full
    if len(s) > SUMMARY_MAX:
        s = re.sub(r"\s+\S*$", "", s[:SUMMARY_MAX]).strip()
    return s, len(s) < len(full)


def split_descriptions(payload: list[dict]) -> dict[str, str]:
    """Replace each event's `description` with `summary` (+ `more`), in
    place; return {id: full description} for the events with more."""
    full = {}
    for e in payload:
        text = e.pop("description", None)
        summary, more = summarize(text)
        if summary:
            e["summary"] = summary
        if more:
            e["more"] = True
            full[e["id"]] = re.sub(r"\s+", " ", text).strip()
    return full


def _is_food_drink_only(serialized: dict) -> bool:
    """True when the event's only classified format is social/food-drink.

    Untagged events (empty types) are never food/drink-only, so they're kept.
    """
    types = serialized.get("types") or []
    return bool(types) and all(path == _FOOD_DRINK_TYPE for path in types)


def _serialize(event: Event, cache: Cache, venues: VenueStore | None = None) -> dict:
    # Join each event to its show's classification on (source, title). An event
    # may carry several sources; use the first that has a cache entry.
    entry = None
    for src in event.sources or []:
        entry = cache.get(src, event.title)
        if entry is not None:
            break
    place = venues.entry(event.location) if venues else None
    out = {
        "id": str(event.id),
        "title": event.title,
        "start_time": event.start_time.isoformat(),
        "location": event.location,
        "url": event.url,
        "description": event.description,
        "image_url": event.image_url,
        # sources is a list — a single event may be listed by multiple sources
        # (e.g. A.C.T. presents a show that ATG also lists).
        "sources": list(event.sources or []),
        # Tags, joined from the classification cache (empty when untagged).
        "types": entry.types if entry else [],
        "topics": entry.topics if entry else [],
        "cost": entry.cost if entry else "unknown",
        # Where it is, joined from the committed venue files (feature-specs/
        # venues.md) — null when unknown, pending, or not a single place.
        "venue": place.get("venue") if place else None,
        "region": venues.region_of(event.location) if venues else None,
    }
    if place and place.get("venue") and place.get("room"):
        out["room"] = place["room"]  # only when named: most events have none
    return out


def _venue_summary(v: dict) -> dict:
    out = {"name": v["name"], "region": v["region"]}
    if v.get("address"):
        out["address"] = v["address"]
    return out


def _load_venues(venues_dir: Path) -> VenueStore | None:
    """The venue files, or None when they don't validate — a broken hand
    edit must not ship wrong areas, so the manifest goes without them
    (and the merge job's places check alerts)."""
    store = VenueStore(venues_dir)
    errors = store.validate()
    if errors:
        print(f"[export] venue files invalid ({len(errors)} problem(s)); exporting without areas",
              flush=True)
        return None
    return store


def export_json(
    path: Path,
    back_window_days: int = DEFAULT_BACK_WINDOW_DAYS,
    classifications_path: Path = DEFAULT_CLASSIFICATIONS,
    venues_dir: Path = VENUES_DIR,
) -> int:
    """Write upcoming events as a JSON manifest to `path`.

    `back_window_days` is measured in **calendar days** in EXPORT_TZ, not
    rolling 24-hour periods:
      - 1 (default) → include events whose local day is today or later; an
        event that started this morning stays visible all day.
      - 2 → today + yesterday + future.
      - 0 → tomorrow onward only.

    Returns the number of events written.
    """
    today_local = datetime.now(EXPORT_TZ).date()
    oldest_day = today_local - timedelta(days=max(0, back_window_days - 1))
    cutoff = datetime.combine(oldest_day, dtime.min, tzinfo=EXPORT_TZ).astimezone(timezone.utc)
    cache = Cache(classifications_path)
    venues = _load_venues(venues_dir)
    session = get_session()
    try:
        events = (
            session.query(Event)
            .filter(Event.start_time >= cutoff)
            # id is a stable tiebreaker for events sharing a start_time, so
            # re-exporting the same data is byte-identical (minimal git diffs).
            .order_by(Event.start_time, Event.id)
            .all()
        )
        payload = [_serialize(e, cache, venues) for e in events]
    finally:
        session.close()

    # Drop food/drink-only listings (happy hours, lunch specials, tastings)
    # after tags are joined — they're off-target for the aggregator.
    kept = [e for e in payload if not _is_food_drink_only(e)]
    dropped = len(payload) - len(kept)
    if dropped:
        print(f"[export] dropped {dropped} food/drink-only events", flush=True)
    payload = kept

    descriptions = split_descriptions(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # The taxonomy travels with the manifest so the static frontend builds
        # its type/topic filters from the same source of truth (no extra fetch).
        "taxonomy": taxonomy.load_taxonomy(),
        # Area filter: the regions in display order, and the venues events
        # point at (stored once, not per event).
        "regions": [{"id": r, "label": label} for r, label in REGIONS.items()],
        "venues": {vid: _venue_summary(venues.venues[vid])
                   for vid in sorted({e["venue"] for e in payload if e["venue"]})} if venues else {},
        "events": payload,
    }
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    # Beside events.json; one event per line (in event order) keeps the
    # data PR diffs line-based like the manifest's.
    with open(path.with_name(DESCRIPTIONS_FILE), "w") as f:
        json.dump(descriptions, f, indent=0, ensure_ascii=False)
    return len(payload)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Export events as a JSON manifest.")
    parser.add_argument("--out", type=Path, default=Path("events.json"))
    parser.add_argument(
        "--back-window-days",
        type=int,
        default=DEFAULT_BACK_WINDOW_DAYS,
        help="Include events whose LOCAL calendar day is within this many days "
             "in the past. 1 (default) = today onward, 2 = today+yesterday, etc.",
    )
    args = parser.parse_args()
    count = export_json(args.out, back_window_days=args.back_window_days)
    print(f"wrote {count} events to {args.out}")


if __name__ == "__main__":
    _cli()
