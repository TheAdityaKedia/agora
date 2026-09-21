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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db import get_session
from models import Event


DEFAULT_BACK_WINDOW_DAYS = 1


def _serialize(event: Event) -> dict:
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
    }


def export_json(path: Path, back_window_days: int = DEFAULT_BACK_WINDOW_DAYS) -> int:
    """Write upcoming events as a JSON manifest to `path`.

    Includes events with `start_time >= now - back_window_days`. Returns the
    number of events written.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=back_window_days)
    session = get_session()
    try:
        events = (
            session.query(Event)
            .filter(Event.start_time >= cutoff)
            .order_by(Event.start_time)
            .all()
        )
        payload = [_serialize(e) for e in events]
    finally:
        session.close()

    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
        help="Include events whose start_time is within this many days in the past.",
    )
    args = parser.parse_args()
    count = export_json(args.out, back_window_days=args.back_window_days)
    print(f"wrote {count} events to {args.out}")


if __name__ == "__main__":
    _cli()
