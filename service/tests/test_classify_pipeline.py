"""Tests for the cache-aware batch step: select_shows + classify_new_shows."""
import classify
from classify import select_shows, classify_new_shows, PRIMARY_MODEL
from classifications import Cache, Classification


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
        cost="free", model=PRIMARY_MODEL, taxonomy_version=1,
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
    # seed a stale entry (taxonomy_version 0 < current 1)
    cache.put(Classification(
        title="Old", source="City Lights Booksellers", types=[["talk"]], topics=[],
        cost="unknown", model=PRIMARY_MODEL, taxonomy_version=0,
        classified_at="2026-01-01T00:00:00+00:00",
    ))
    shows = [{"source": "City Lights Booksellers", "title": "Old", "description": "d"}]
    classified, cached = classify_new_shows(shows, cache, classifier=_fake_classifier)
    assert (classified, cached) == (1, 0)
    assert cache.get("City Lights Booksellers", "Old").taxonomy_version == 1


def test_classify_new_shows_saves_cache_to_disk(tmp_path):
    path = tmp_path / "c.json"
    cache = Cache(path)
    shows = [{"source": "City Lights Booksellers", "title": "T", "description": "d"}]
    classify_new_shows(shows, cache, classifier=_fake_classifier)
    # a fresh Cache reads the persisted entry
    assert Cache(path).get("City Lights Booksellers", "T") is not None
