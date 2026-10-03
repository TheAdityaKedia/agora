"""CLI: python -m places {validate,resolve,report}

  validate                      check the committed venue files
  resolve --manifest PATH       resolve every location string in a manifest
                                that isn't known yet (and pending ones due a retry)
  report  --manifest PATH       list venues and pending strings for review
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .geocode import Nominatim
from .pipeline import collect_locations
from .resolve import Resolver
from .store import DATA_DIR, Store


def cmd_validate(args) -> int:
    errs = Store(args.data_dir).validate()
    for e in errs:
        print(e)
    print(f"{len(errs)} problem(s)")
    return 1 if errs else 0


def cmd_resolve(args) -> int:
    store = Store(args.data_dir)
    events = json.loads(Path(args.manifest).read_text())["events"]
    locs = collect_locations(events)
    geo = Nominatim(cache_path=args.cache, max_calls=args.max_lookups)
    resolver = Resolver(store, geo, retry_pending=args.retry_pending)
    counts = Counter()
    for key, info in sorted(locs.items(), key=lambda kv: -kv[1]["events"]):
        out = resolver.resolve(info["text"], info["sources"], info["events"])
        counts[out.action] += 1
        if out.action not in ("known",):
            detail = out.reason or ", ".join(out.evidence)
            print(f"{out.action:8} {info['events']:4}  {info['text'][:70]!r}  {detail}", flush=True)
    store.save()
    print(dict(counts), f"lookups={geo.calls}")
    return 0


def cmd_report(args) -> int:
    store = Store(args.data_dir)
    events = json.loads(Path(args.manifest).read_text())["events"]
    locs = collect_locations(events)
    by_region = Counter()
    for info in locs.values():
        by_region[store.region_of(info["text"]) or "unknown"] += info["events"]
    total = sum(by_region.values())
    print(f"events with a location: {total}")
    for r, n in by_region.most_common():
        print(f"  {r:10} {n:5}  {100 * n / total:.1f}%")
    pending = [(k, e["pending"]) for k, e in store.locations.items() if "pending" in e]
    print(f"pending strings: {len(pending)}")
    for k, p in sorted(pending, key=lambda kp: -kp[1].get("events", 0)):
        print(f"  {p.get('events', 0):4}  {k!r}: {p['reason']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m places")
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    r = sub.add_parser("resolve")
    r.add_argument("--manifest", required=True)
    r.add_argument("--cache", type=Path, help="dev only: on-disk Nominatim response cache")
    r.add_argument("--max-lookups", type=int, default=None)
    r.add_argument("--retry-pending", action="store_true",
                   help="retry pending strings now instead of weekly")
    p = sub.add_parser("report")
    p.add_argument("--manifest", required=True)
    args = ap.parse_args(argv)
    return {"validate": cmd_validate, "resolve": cmd_resolve, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
