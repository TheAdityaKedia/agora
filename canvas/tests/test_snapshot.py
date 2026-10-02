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
        return {e["id"]: e for e in EVENTS["events"]}

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
    assert snapshot._fetch()["a"]["title"] == "A"
