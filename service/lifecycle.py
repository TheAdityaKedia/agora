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
from datetime import datetime, timedelta, timezone

SCHEDULED, CANCELLED, POSTPONED, UNLISTED, MOVED = (
    "scheduled", "cancelled", "postponed", "unlisted", "moved")
EXPLICIT = (CANCELLED, POSTPONED)        # what a scraper can say
LISTED = (SCHEDULED, CANCELLED, POSTPONED)  # what the manifest shows
GONE = (UNLISTED, MOVED)

# The fields the creating source owns; a change to any is recorded.
TRACKED_FIELDS = ("title", "location", "url", "description", "image_url")

# How long a change stays worth showing. Here rather than in the exporter
# because apply_update needs it too: it keeps unexpired entries in `changed`.
CHANGED_DAYS = 7

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
    an unlisted (or moved) row is back: it was a flaky scrape.

    A row that comes back from `moved` also needs its move undone — see
    undo_move, which the save path calls because it needs the session.
    """
    row.seen = {**(row.seen or {}), source: _iso(now)}  # reassign: JSON isn't tracked in place
    row.misses = {**(row.misses or {}), source: 0}
    if row.status in GONE:
        set_status(row, SCHEDULED, now)


def undo_move(session, row) -> bool:
    """The source lists this row again after it was judged moved: the pairing
    was wrong (a flaky scrape, or the old showing came back). Restoring the row
    alone would leave both showings listed while an alias still redirected this
    id to the other one and the other one still claimed "Rescheduled · was
    <this row's time>". Drop that alias and that marker. Returns whether
    anything was undone.
    """
    from models import Event, EventAlias  # local: models imports nothing of ours

    alias = session.get(EventAlias, row.id)
    if alias is None or alias.reason != MOVED:
        return False
    successor = session.get(Event, alias.new_id)
    was = (successor.changed or {}).get("start_time") if successor is not None else None
    if was and _aware(datetime.fromisoformat(was)) == _aware(row.start_time):
        rest = {k: v for k, v in successor.changed.items() if k != "start_time"}
        successor.changed = rest or None
        if not rest:
            successor.changed_at = None
    session.delete(alias)
    return True


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
        # Merge, don't replace: a description-only re-scrape must not erase a
        # live "Rescheduled · was …" or "Venue changed" badge (only some
        # tracked fields are shown). Entries already recorded win, so what's
        # shown stays the value from before the first change in the window;
        # entries whose window has passed are dropped.
        prior = row.changed or {}
        if not prior or not row.changed_at or now - _aware(row.changed_at) > timedelta(days=CHANGED_DAYS):
            prior = {}
        row.changed, row.changed_at = {**was, **prior}, now
    # An explicit status applies at once; listed again without one, it's on.
    flipped = False
    if raw.status in EXPLICIT:
        flipped = set_status(row, raw.status, now)
    elif row.status in EXPLICIT:
        flipped = set_status(row, SCHEDULED, now)
    return bool(was) or flipped


# --- disappearances (§3): run by the CI merge after every source is saved ---

# A scrape that misses more than this share (and more than PARTIAL_MIN) of
# the rows it should have listed is treated as partial: no misses counted.
PARTIAL_SHARE = 0.2
PARTIAL_MIN = 5
UNLISTED_AFTER = 2  # consecutive good scrapes of every source, all without it


def _aware(t: datetime) -> datetime:
    """SQLite hands back naive datetimes (stored as UTC)."""
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _seen_since(row, source: str, since: datetime) -> bool:
    at = (row.seen or {}).get(source)
    return bool(at) and _aware(datetime.fromisoformat(at)) >= since


def judge_disappearances(session, returned: dict[str, list[datetime]], run_start: datetime,
                         now: datetime | None = None) -> dict:
    """Count misses, mark rows unlisted, pair reschedules; commits.

    `returned`: for each source whose scrape this run was good, the start
    times of the events it returned. A row was listed this run by a source
    iff save_events marked it seen by that source since `run_start`.

    - Window: a source is judged only on rows with now < start <= the latest
      start it returned (a source showing 3 weeks says nothing about week 5).
    - Only scheduled rows are judged. Cancelled/postponed ones stay as they
      are until their date (more informative than "unlisted"), and rows
      already gone would otherwise count as missing forever.
    - Partial guard: missing more than PARTIAL_SHARE (and more than
      PARTIAL_MIN) of its judged rows → the source is "possibly partial":
      no misses this run.
    - A row becomes unlisted when every source on it was judged on it this
      run and each has misses >= UNLISTED_AFTER. A source that failed, wasn't
      in the run, or was partial blocks the decision.
    - Reschedule: an unlisted row from S whose URL S now lists at exactly one
      new start (a row S created since it last saw the old one), with no other
      live row of S on that URL in the window → the old row is moved, with a
      moved alias old → new, and the new row's changed = {start_time: old}.
    """
    import event_ids
    from models import Event

    now = now or datetime.now(timezone.utc)
    upcoming = [e for e in session.query(Event).filter(Event.start_time > now).all()]
    report = {"unlisted": 0, "moved": 0, "possibly_partial": []}
    judged: dict = {}  # row id → sources that judged it this run
    rows = {}
    windows: dict[str, datetime] = {}
    for source, starts in returned.items():
        if not starts:
            continue  # nothing returned: no window
        top = max(_aware(t) for t in starts)
        windows[source] = top
        window = [e for e in upcoming if source in (e.sources or [])
                  and _aware(e.start_time) <= top and (e.status or SCHEDULED) == SCHEDULED]
        missing = [e for e in window if not _seen_since(e, source, run_start)]
        if len(missing) > PARTIAL_SHARE * len(window) and len(missing) > PARTIAL_MIN:
            report["possibly_partial"].append(source)
            continue
        for e in window:
            judged.setdefault(e.id, set()).add(source)
            rows[e.id] = e
        for e in missing:
            m = dict(e.misses or {})
            m[source] = m.get(source, 0) + 1
            e.misses = m

    gone = []
    for rid, e in rows.items():
        sources = set(e.sources or [])
        if sources <= judged[rid] and all((e.misses or {}).get(s, 0) >= UNLISTED_AFTER for s in sources):
            set_status(e, UNLISTED, now)
            gone.append(e)
    report["unlisted"] = len(gone)

    taken = set()
    for old in gone:
        s = old.sources[0]
        if not old.url or s not in windows:
            continue
        last = (old.seen or {}).get(s)
        since = _aware(datetime.fromisoformat(last)) if last else _aware(old.created_at)
        new = [e for e in upcoming if e.url == old.url and e.id != old.id
               and (e.sources or [None])[0] == s and _aware(e.start_time) != _aware(old.start_time)
               and _aware(e.created_at) > since and (e.status or SCHEDULED) == SCHEDULED]
        if len(new) != 1 or new[0].id in taken:
            continue
        others = [e for e in upcoming if e.url == old.url and e.id not in (old.id, new[0].id)
                  and s in (e.sources or []) and _aware(e.start_time) <= windows[s]
                  and (e.status or SCHEDULED) not in GONE]
        if others:
            continue  # a URL with several showings: ambiguous, stays unlisted
        n = new[0]
        taken.add(n.id)
        set_status(old, MOVED, now)
        event_ids.add_alias(session, old.id, n.id, "moved", now)
        n.changed = {"start_time": _aware(old.start_time).isoformat()}
        n.changed_at = now
        report["moved"] += 1
    report["unlisted"] -= report["moved"]
    session.commit()
    return report
