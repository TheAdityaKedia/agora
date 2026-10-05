import gzip
import json

import pytest

import snapshot

EVENTS = {"events": [{"id": "a", "title": "A", "start_time": "2026-10-10T19:00:00+00:00",
                      "description": "long", "image_url": "", "sources": ["X"]}]}


@pytest.fixture
def fetches(monkeypatch):
    calls = []

    def fake_fetch():
        calls.append(1)
        return {"events": {e["id"]: e for e in EVENTS["events"]}, "gone": {}, "aliases": {},
                "overlay": True}

    monkeypatch.setattr(snapshot, "_fetch", fake_fetch)
    snapshot.reset_cache()
    return calls


def test_snapshot_keeps_display_fields_only():
    snap = snapshot.snapshot_of(EVENTS["events"][0])
    assert snap == {"id": "a", "title": "A", "start_time": "2026-10-10T19:00:00+00:00",
                    "sources": ["X"]}


def test_cached_within_ttl(fetches):
    assert snapshot.lookup("a", now=1000)["title"] == "A"
    snapshot.lookup("a", now=1000 + snapshot.CACHE_TTL_S - 1)
    assert len(fetches) == 1
    snapshot.lookup("a", now=1000 + snapshot.CACHE_TTL_S + 1)
    assert len(fetches) == 2


def test_miss_refetches_at_most_once_per_window(fetches):
    snapshot.lookup("a", now=1000)
    assert snapshot.lookup("new", now=1010) is None
    assert len(fetches) == 1  # too soon after the last fetch
    snapshot.lookup("new", now=1000 + snapshot.MISS_REFETCH_S + 1)
    assert len(fetches) == 2


def test_stale_copy_served_when_fetch_fails(fetches, monkeypatch):
    snapshot.lookup("a", now=1000)

    def fail():
        raise OSError("down")

    monkeypatch.setattr(snapshot, "_fetch", fail)
    assert snapshot.lookup("a", now=1000 + snapshot.CACHE_TTL_S + 1)["title"] == "A"


def test_unavailable_without_any_copy(monkeypatch):
    snapshot.reset_cache()

    def fail():
        raise OSError("down")

    monkeypatch.setattr(snapshot, "_fetch", fail)
    with pytest.raises(snapshot.ManifestUnavailable):
        snapshot.lookup("a")


def test_fetch_handles_gzip(tmp_path, monkeypatch):
    # file:// responses are never gzip-encoded; exercise the decode path directly.
    class Resp:
        headers = {"Content-Encoding": "gzip"}

        def read(self):
            return gzip.compress(json.dumps(EVENTS).encode())

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(snapshot.urllib.request, "urlopen", lambda req, timeout: Resp())
    assert snapshot._get("https://x/events.json") == EVENTS


def test_reads_the_index_beside_the_manifest_and_falls_back_to_the_manifest(monkeypatch):
    monkeypatch.setenv("MANIFEST_URL", "https://pages/agora/events.json")
    monkeypatch.delenv("INDEX_URL", raising=False)
    asked = []
    index = {"events": {"a": {"title": "A"}}, "gone": {"g": {"status": "unlisted"}}, "aliases": {"o": "a"}}

    def get(url):
        asked.append(url)
        if url.endswith("event-index.json") and index is not None:
            return index
        if url.endswith("events.json"):
            return EVENTS
        raise OSError("404")

    monkeypatch.setattr(snapshot, "_get", get)
    got = snapshot._fetch()
    assert asked == ["https://pages/agora/event-index.json"]
    assert got["overlay"] and got["events"]["a"] == {"title": "A", "id": "a"} and got["aliases"] == {"o": "a"}
    index = None  # not published yet
    got = snapshot._fetch()
    assert asked[-2:] == ["https://pages/agora/event-index.json", "https://pages/agora/events.json"]
    assert not got["overlay"] and got["events"]["a"]["title"] == "A" and got["gone"] == {}
