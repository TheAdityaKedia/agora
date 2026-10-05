"""Server-built event snapshots, and the current state of events.

Anyone with a canvas link can edit it, so the browser never supplies event
data: it sends an `event_id` and we copy the fields from what Agora
published. Event items are therefore always real Agora data, and the copy
(the "as added" record) outlives the listing.

What we read is `event-index.json` (feature-specs/event-lifecycle.md, §4):
current facts by id (no descriptions), the events that are gone (unlisted,
moved) until their date, and aliases for ids that changed. Until that file
is published (or if it can't be fetched) we fall back to `events.json`,
which can add events but knows nothing about changes: no overlay then.

The index is cached per warm Lambda container for CACHE_TTL_S and
re-fetched early on a miss (an event newer than our copy), at most once per
MISS_REFETCH_S so unknown IDs can't make us hammer Pages.
"""
import gzip
import json
import os
import re
import time
import urllib.request

DEFAULT_MANIFEST_URL = "https://theadityakedia.github.io/agora/events.json"
INDEX_FILE = "event-index.json"
# SNAPSHOT_CACHE_TTL_S: only for local runs (the e2e changes the index mid-run).
CACHE_TTL_S = int(os.environ.get("SNAPSHOT_CACHE_TTL_S") or 600)
MISS_REFETCH_S = 60
SNAPSHOT_FIELDS = ("id", "title", "start_time", "location", "url", "image_url", "sources", "venue")
# What the overlay compares between the copy and the current event.
NOW_FIELDS = ("start_time", "location", "title", "url")
MAX_ALIAS_HOPS = 20

_cache = {"index": None, "fetched_at": 0.0}


class ManifestUnavailable(Exception):
    pass


def _urls():
    manifest = os.environ.get("MANIFEST_URL") or DEFAULT_MANIFEST_URL
    index = os.environ.get("INDEX_URL") or manifest.rsplit("/", 1)[0] + "/" + INDEX_FILE
    return index, manifest


def _get(url):
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw)


def _fetch():
    """{"events": {id: event}, "gone": {...}, "aliases": {...}, "overlay": bool}."""
    index_url, manifest_url = _urls()
    try:
        data = _get(index_url)
        return {"events": {i: dict(e, id=i) for i, e in (data.get("events") or {}).items()},
                "gone": data.get("gone") or {}, "aliases": data.get("aliases") or {},
                "overlay": True}
    except Exception:  # not published yet, or unreachable: the manifest can still add
        data = _get(manifest_url)
        return {"events": {e["id"]: e for e in data.get("events", []) if e.get("id")},
                "gone": {}, "aliases": {}, "overlay": False}


def _refresh(now):
    try:
        _cache["index"] = _fetch()
    except Exception as e:  # network, HTTP, bad JSON
        if _cache["index"] is None:
            raise ManifestUnavailable(type(e).__name__) from None
        # keep serving the stale copy; try again after MISS_REFETCH_S
    _cache["fetched_at"] = now


def _index(event_id=None, now=None):
    now = time.time() if now is None else now
    age = now - _cache["fetched_at"]
    idx = _cache["index"]
    if idx is None or age > CACHE_TTL_S:
        _refresh(now)
    elif event_id is not None and age > MISS_REFETCH_S and resolve(idx, event_id) not in idx["events"]:
        _refresh(now)
    return _cache["index"]


def resolve(idx, event_id):
    """The current id for `event_id` (aliases are already resolved by the
    exporter; the hop limit only guards against a bad file)."""
    cur = event_id
    for _ in range(MAX_ALIAS_HOPS):
        nxt = idx["aliases"].get(cur)
        if nxt is None or nxt == cur:
            break
        cur = nxt
    return cur


def lookup(event_id, now=None):
    """The listed event this id (or an old alias of it) is now, or None."""
    idx = _index(event_id, now)
    return idx["events"].get(resolve(idx, event_id))


def aliases_of(event_id, now=None):
    """Old ids that lead to this event id (for the duplicate check)."""
    idx = _index(None, now)
    cur = resolve(idx, event_id)
    return [old for old in idx["aliases"] if old != event_id and resolve(idx, old) == cur]


_STREET_NUMBER = re.compile(r"\b(\d{1,5})\s+(?=\d{0,3}[A-Za-z])")


def same_place(old, new):
    """Whether two location strings are one place written two ways. Venue
    ids (from the venue files) decide when both copies have one; otherwise
    a shared street number, or one's venue name (before the first comma)
    inside the other: a small port of the pipeline's dedup.locations_agree."""
    a, b = (old or {}).get("location"), (new or {}).get("location")
    if (old or {}).get("venue") and (new or {}).get("venue"):
        return old["venue"] == new["venue"]
    if not (a and b):
        return True  # one side unknown: nothing to say
    if set(_STREET_NUMBER.findall(a)) & set(_STREET_NUMBER.findall(b)):
        return True
    na, nb = a.casefold(), b.casefold()
    va, vb = na.split(",")[0].strip(), nb.split(",")[0].strip()
    return bool(va and va in nb) or bool(vb and vb in na)


def current(stored, now=None):
    """The `now` overlay for an item's stored copy: its status and only what
    differs ({status, start_time?, location?, venue_changed?, title?, url?,
    moved_to?, current_id?}), or None when unknown (index unavailable, or the
    event has left the listing: passed or dropped). `location` is the current
    text whenever it differs; `venue_changed` only when it's another place,
    not the same one written differently."""
    try:
        idx = _index(None, now)
    except ManifestUnavailable:
        return None
    if not idx.get("overlay") or not stored or not stored.get("id"):
        return None
    eid = stored["id"]
    cur = resolve(idx, eid)
    if cur in idx["events"]:
        ev = idx["events"][cur]
        out = {"status": ev.get("status") or "scheduled"}
        out.update({k: ev[k] for k in NOW_FIELDS if ev.get(k) and ev.get(k) != stored.get(k)})
        if "location" in out and not same_place(stored, ev):
            out["venue_changed"] = True
    elif cur in idx["gone"]:
        g = idx["gone"][cur]
        out = {"status": g.get("status") or "unlisted"}
        to = g.get("moved_to")
        if to:
            out["moved_to"] = to
            new = idx["events"].get(to)
            if new:  # the new showing's time and place, for "Moved to Sat 8 PM"
                out.update({k: new[k] for k in ("start_time", "location") if new.get(k)})
    else:
        return None
    if cur != eid:
        out["current_id"] = cur
    return out


def snapshot_of(event):
    return {k: event.get(k) for k in SNAPSHOT_FIELDS if event.get(k) not in (None, "", [])}


def reset_cache():
    _cache["index"] = None
    _cache["fetched_at"] = 0.0
