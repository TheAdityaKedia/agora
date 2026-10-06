"""One-off: give every row its stable id (feature-specs/event-lifecycle.md, §1).

    python rekey_events.py            # dry run: print the plan, change nothing
    python rekey_events.py --apply    # re-key in one transaction (needs DATABASE_URL)

Rows saved before stable ids have random ids. For each row whose id isn't
its computed one (event_ids.id_of_row: the creating source's key), change
the id and record a `rekeyed` alias old → new, so collections and calendar
entries holding the old id keep resolving. If the computed id already
belongs to another row (or two rows compute the same id), that is a missed
duplicate: it's reported and both rows are left alone.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field

import event_ids
from models import Event, EventAlias


@dataclass
class Plan:
    rekey: list[tuple[Event, object]] = field(default_factory=list)   # (row, new id)
    collisions: list[tuple[Event, object, str]] = field(default_factory=list)  # (row, id, why)
    unchanged: int = 0


def plan(session) -> Plan:
    out = Plan()
    rows = session.query(Event).order_by(Event.start_time, Event.id).all()
    ids = {e.id for e in rows}
    wanted: dict = {}
    for e in rows:
        # Already keyed at creation: leave it. id_of_row recomputes from the
        # row's *current* url and title, but the id was computed from the
        # values at creation — a row whose url has since been corrected by an
        # update would otherwise be re-keyed on every pass, churning ids that
        # collections and calendar entries hold. Derived ids are uuid5; a row
        # saved before stable ids (or after a collision) has a random uuid4.
        if getattr(e.id, "version", None) == 5:
            out.unchanged += 1
            continue
        new = event_ids.id_of_row(e)
        if new is None or new == e.id:
            out.unchanged += 1
            continue
        wanted.setdefault(new, []).append(e)
    for new, claimants in wanted.items():
        if new in ids:
            for e in claimants:
                out.collisions.append((e, new, "computed id belongs to another row"))
        elif len(claimants) > 1:
            for e in claimants:
                out.collisions.append((e, new, f"{len(claimants)} rows compute this id"))
        else:
            out.rekey.append((claimants[0], new))
    return out


def apply(session, p: Plan) -> int:
    """Change the ids and record aliases, in one transaction."""
    try:
        for e, new in p.rekey:
            old = e.id
            session.query(Event).filter(Event.id == old).update(
                {Event.id: new}, synchronize_session=False)
            # Shorten chains: aliases that pointed at the old id now point here.
            session.query(EventAlias).filter(EventAlias.new_id == old).update(
                {EventAlias.new_id: new}, synchronize_session=False)
            event_ids.add_alias(session, old, new, "rekeyed")
        session.commit()
    except Exception:
        session.rollback()
        raise
    return len(p.rekey)


def cli(argv=None) -> None:
    from db import get_session, init_db

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="re-key (default: dry run)")
    args = parser.parse_args(argv)
    init_db()  # the event_aliases table, if this runs before any scrape
    session = get_session()
    try:
        p = plan(session)
        for e, new, why in p.collisions:
            print(f"collision {e.start_time:%Y-%m-%d %H:%M}Z [{(e.sources or ['?'])[0]}] "
                  f"{e.title[:60]!r}: {e.id} → {new} ({why})")
        print(f"{len(p.rekey)} rows to re-key, {p.unchanged} already stable, "
              f"{len(p.collisions)} collisions left alone"
              + ("" if args.apply else " (dry run — nothing changed)"))
        if args.apply:
            print(f"applied: {apply(session, p)} rows re-keyed")
    finally:
        session.close()


if __name__ == "__main__":
    cli()
