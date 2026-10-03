"""Tests for the cache-aware batch step: select_shows + classify_new_shows."""
import classify
import taxonomy
from classify import select_shows, classify_new_shows, PRIMARY_MODEL
from classifications import Cache, Classification

CURRENT = taxonomy.CURRENT_TAXONOMY_VERSION


def test_select_shows_dedupes_and_picks_richest_description():
    rows = [
        ("SFJAZZ Center", "Show A", "short"),
        ("SFJAZZ Center", "Show A", "a much longer richer description here"),
        ("City Lights Booksellers", "Show B", ""),
    ]
    shows = select_shows(rows)
    by_title = {s["title"]: s for s in shows}
    assert len(shows) == 2
    assert by_title["Show A"]["description"] == "a much longer richer description here"
    assert by_title["Show B"]["source"] == "City Lights Booksellers"


def _fake_classifier(title, source, description, *, client=None):
    return Classification(
        title=title, source=source, types=[["talk"]], topics=["books-authors"],
        cost="free", model=PRIMARY_MODEL, taxonomy_version=CURRENT,
        classified_at="2026-09-23T00:00:00+00:00",
    )


def test_classify_new_shows_classifies_all_on_empty_cache(tmp_path):
    cache = Cache(tmp_path / "c.json")
    shows = [
        {"source": "City Lights Booksellers", "title": "Talk 1", "description": "d"},
        {"source": "City Lights Booksellers", "title": "Talk 2", "description": "d"},
    ]
    classified, cached = classify_new_shows(shows, cache, classifier=_fake_classifier)
    assert (classified, cached) == (2, 0)
    assert cache.get("City Lights Booksellers", "Talk 1").topics == ["books-authors"]


def test_classify_new_shows_skips_cache_hits_on_second_run(tmp_path):
    cache = Cache(tmp_path / "c.json")
    shows = [{"source": "City Lights Booksellers", "title": "Talk 1", "description": "d"}]
    classify_new_shows(shows, cache, classifier=_fake_classifier)
    # second run: same taxonomy version → no LLM calls
    calls = []
    def counting(title, source, description, *, client=None):
        calls.append(title)
        return _fake_classifier(title, source, description)
    classified, cached = classify_new_shows(shows, cache, classifier=counting)
    assert (classified, cached) == (0, 1)
    assert calls == []


def test_classify_new_shows_reclassifies_when_taxonomy_version_stale(tmp_path):
    cache = Cache(tmp_path / "c.json")
    # seed a stale entry (taxonomy_version 0 < current)
    cache.put(Classification(
        title="Old", source="City Lights Booksellers", types=[["talk"]], topics=[],
        cost="unknown", model=PRIMARY_MODEL, taxonomy_version=0,
        classified_at="2026-01-01T00:00:00+00:00",
    ))
    shows = [{"source": "City Lights Booksellers", "title": "Old", "description": "d"}]
    classified, cached = classify_new_shows(shows, cache, classifier=_fake_classifier)
    assert (classified, cached) == (1, 0)
    assert cache.get("City Lights Booksellers", "Old").taxonomy_version == CURRENT


def test_classify_new_shows_saves_cache_to_disk(tmp_path):
    path = tmp_path / "c.json"
    cache = Cache(path)
    shows = [{"source": "City Lights Booksellers", "title": "T", "description": "d"}]
    classify_new_shows(shows, cache, classifier=_fake_classifier)
    # a fresh Cache reads the persisted entry
    assert Cache(path).get("City Lights Booksellers", "T") is not None


# --- concurrency, time budget, failures (a taxonomy bump re-tags everything) ---

def _shows(n):
    return [{"source": "S", "title": f"Show {i}", "description": ""} for i in range(n)]


def test_classify_new_shows_runs_concurrently(tmp_path):
    import threading
    import time
    active, peak, lock = [0], [0], threading.Lock()

    def slow(title, source, description, *, client=None):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.02)
        with lock:
            active[0] -= 1
        return _fake_classifier(title, source, description)

    cache = Cache(tmp_path / "c.json")
    classified, _ = classify_new_shows(_shows(40), cache, classifier=slow, workers=8, log=None)
    assert classified == 40 and 1 < peak[0] <= 8
    assert len(Cache(tmp_path / "c.json")) == 40


def test_classify_new_shows_stops_starting_calls_at_the_time_budget(tmp_path):
    now = [0.0]

    def ticking(title, source, description, *, client=None):
        now[0] += 60  # each call "takes" a minute
        return _fake_classifier(title, source, description)

    cache = Cache(tmp_path / "c.json")
    logs = []
    classified, _ = classify_new_shows(_shows(100), cache, classifier=ticking, workers=1,
                                       time_budget_s=10 * 60, clock=lambda: now[0], log=logs.append)
    assert 0 < classified < 100
    assert "left for the next run" in logs[-1]
    # What was done is saved: the next run only does the rest.
    again = Cache(tmp_path / "c.json")
    rest, cached = classify_new_shows(_shows(100), again, classifier=_fake_classifier, log=None)
    assert cached == classified and rest == 100 - classified


def test_one_failing_show_does_not_lose_the_batch(tmp_path):
    def flaky(title, source, description, *, client=None):
        if title == "Show 3":
            raise RuntimeError("all models failed")
        return _fake_classifier(title, source, description)

    cache = Cache(tmp_path / "c.json")
    classified, _ = classify_new_shows(_shows(10), cache, classifier=flaky, log=None)
    assert classified == 9
    assert Cache(tmp_path / "c.json").get("S", "Show 3") is None


def test_unreachable_model_gives_up_early(tmp_path):
    import pytest
    calls = []

    def down(title, source, description, *, client=None):
        calls.append(title)
        raise RuntimeError("NoCredentialsError")

    with pytest.raises(RuntimeError, match="unreachable"):
        classify_new_shows(_shows(500), Cache(tmp_path / "c.json"), classifier=down, workers=4, log=None)
    assert len(calls) < 40
