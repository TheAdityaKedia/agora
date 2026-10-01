"""One-off: merge cross-source duplicates saved before fuzzy dedup existed.

    python dedupe_existing.py            # dry run: print the plan, change nothing
    python dedupe_existing.py --apply    # merge + delete (needs DATABASE_URL)

Uses the same rule as save-time dedup (dedup.is_near_duplicate, same start
time, rows from different sources). In each cluster the row whose first
source comes earliest in sources.txt is kept (what save order would have
kept); the others' sources are appended to it and they are deleted.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import dedup
from models import Event


@dataclass
class Merge:
    keep: Event
    drop: list[Event]
    sources: list[str]


def _rank(event: Event, order: list[str]) -> int:
    first = (event.sources or [None])[0]
    return order.index(first) if first in order else len(order)


def plan(session, source_order: list[str]) -> list[Merge]:
    by_start: dict = {}
    for e in session.query(Event).order_by(Event.start_time, Event.id).all():
        by_start.setdefault(e.start_time, []).append(e)
    merges = []
    for events in by_start.values():
        if len(events) < 2:
            continue
        parent = {e.id: e.id for e in events}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, a in enumerate(events):
            for b in events[i + 1:]:
                if set(a.sources or []) & set(b.sources or []):
                    continue  # same source: two listings mean two events
                if dedup.is_near_duplicate(a.title, a.location, b.title, b.location):
                    parent[find(a.id)] = find(b.id)
        clusters: dict = {}
        for e in events:
            clusters.setdefault(find(e.id), []).append(e)
        for members in clusters.values():
            if len(members) < 2:
                continue
            members.sort(key=lambda e: (_rank(e, source_order), str(e.id)))
            keep, drop = members[0], members[1:]
            sources = list(keep.sources or [])
            for d in drop:
                sources += [s for s in (d.sources or []) if s not in sources]
            merges.append(Merge(keep=keep, drop=drop, sources=sources))
    return merges


def apply(session, merges: list[Merge]) -> int:
    for m in merges:
        m.keep.sources = list(m.sources)  # reassign: JSON column mutations aren't tracked
        for d in m.drop:
            session.delete(d)
    session.commit()
    return sum(len(m.drop) for m in merges)


def _source_order() -> list[str]:
    import main
    names = []
    for url in main.load_sources():
        scraper = main.find_scraper(url)
        if scraper and scraper.NAME not in names:
            names.append(scraper.NAME)
    return names


def cli(argv=None) -> None:
    from db import get_session

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="merge and delete (default: dry run)")
    args = parser.parse_args(argv)
    session = get_session()
    try:
        merges = plan(session, _source_order())
        for m in merges:
            print(f"{m.keep.start_time:%Y-%m-%d %H:%M}Z  keep [{m.keep.sources[0]}] {m.keep.title[:60]!r}")
            for d in m.drop:
                print(f"{'':19}drop [{d.sources[0]}] {d.title[:60]!r}")
        print(f"{len(merges)} clusters, {sum(len(m.drop) for m in merges)} rows to delete"
              + ("" if args.apply else " (dry run — nothing changed)"))
        if args.apply:
            print(f"applied: {apply(session, merges)} rows deleted")
    finally:
        session.close()


if __name__ == "__main__":
    cli()
