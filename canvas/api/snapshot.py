"""Server-built event snapshots.

Anyone with a canvas link can edit it, so the browser never supplies event
data: it sends an `event_id` and we copy the fields from the published
`events.json`. Event items are therefore always real Agora data, and the copy
outlives the manifest (past events are pruned; IDs can churn on re-scrape or
dedup merges).

The manifest (~7 MB) is cached per warm Lambda container for CACHE_TTL_S and
re-fetched early on a miss (an event newer than our copy), at most once per
MISS_REFETCH_S so unknown IDs can't make us hammer Pages.
"""
import gzip
import json
import os
import time
import urllib.request

DEFAULT_MANIFEST_URL = "https://theadityakedia.github.io/agora/events.json"
CACHE_TTL_S = 600
MISS_REFETCH_S = 60
SNAPSHOT_FIELDS = ("id", "title", "start_time", "location", "url", "image_url", "sources")

_cache = {"by_id": None, "fetched_at": 0.0}


class ManifestUnavailable(Exception):
    pass


def _fetch():
    url = os.environ.get("MANIFEST_URL") or DEFAULT_MANIFEST_URL
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    data = json.loads(raw)
    return {e["id"]: e for e in data.get("events", []) if e.get("id")}


def _refresh(now):
    try:
        _cache["by_id"] = _fetch()
    except Exception as e:  # network, HTTP, bad JSON
        if _cache["by_id"] is None:
            raise ManifestUnavailable(type(e).__name__) from None
        # keep serving the stale copy; try again after MISS_REFETCH_S
    _cache["fetched_at"] = now


def lookup(event_id, now=None):
    """The manifest event with this id, or None."""
    now = time.time() if now is None else now
    age = now - _cache["fetched_at"]
    if _cache["by_id"] is None or age > CACHE_TTL_S:
        _refresh(now)
    elif event_id not in _cache["by_id"] and age > MISS_REFETCH_S:
        _refresh(now)
    return _cache["by_id"].get(event_id)


def snapshot_of(event):
    return {k: event.get(k) for k in SNAPSHOT_FIELDS if event.get(k) not in (None, "", [])}


def reset_cache():
    _cache["by_id"] = None
    _cache["fetched_at"] = 0.0
