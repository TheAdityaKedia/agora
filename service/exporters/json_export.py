"""JSON exporter for the static-site path.

Reads persisted events from the database and writes a single JSON manifest
suitable for a static frontend (e.g. served from GitHub Pages / S3). Prunes
past events so the file stays small.

Output shape:
    {
      "generated_at": "2026-09-11T18:04:00+00:00",
      "events": [ { ...event... }, ... ]
    }
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import taxonomy
from classifications import Cache
from db import get_session
from models import Event


# Interpret the past-window in CALENDAR DAYS in the source region, not rolling
# 24-hour periods. This is Agora's audience region (SF Bay Area), so we anchor
# to Pacific local time — events that happened earlier today (local) stay in
# the manifest until midnight PT, not until 24h after they started.
EXPORT_TZ = ZoneInfo("America/Los_Angeles")

DEFAULT_BACK_WINDOW_DAYS = 1
DEFAULT_CLASSIFICATIONS = Path(__file__).parent.parent / "data" / "classifications.json"


def _serialize(event: Event, cache: Cache) -> dict:
    # Join each event to its show's classification on (source, title). An event
    # may carry several sources; use the first that has a cache entry.
    entry = None
    for src in event.sources or []:
        entry = cache.get(src, event.title)
        if entry is not None:
            break
    return {
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
    }


def export_json(
    path: Path,
    back_window_days: int = DEFAULT_BACK_WINDOW_DAYS,
    classifications_path: Path = DEFAULT_CLASSIFICATIONS,
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
        payload = [_serialize(e, cache) for e in events]
    finally:
        session.close()

    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # The taxonomy travels with the manifest so the static frontend builds
        # its type/topic filters from the same source of truth (no extra fetch).
        "taxonomy": taxonomy.load_taxonomy(),
        "events": payload,
    }
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
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
