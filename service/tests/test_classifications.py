"""Tests for the committed classification cache (service/classifications.py)."""
import json

import classifications
from classifications import Cache, Classification, show_key


def test_show_key_joins_source_and_title_with_unit_separator():
    assert show_key("SFJAZZ Center", "Branford Marsalis") == "SFJAZZ Center\x1fBranford Marsalis"


def test_put_then_get_roundtrips(tmp_path):
    c = Cache(tmp_path / "classifications.json")
    entry = Classification(
        title="Branford Marsalis Quartet",
        source="SFJAZZ Center",
        types=[["performance"]],
        topics=["jazz"],
        cost="paid",
        model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
        taxonomy_version=1,
        classified_at="2026-09-23T00:00:00+00:00",
    )
    c.put(entry)
    got = c.get("SFJAZZ Center", "Branford Marsalis Quartet")
    assert got is not None
    assert got.types == [["performance"]]
    assert got.topics == ["jazz"]
    assert got.cost == "paid"


def test_get_missing_returns_none(tmp_path):
    c = Cache(tmp_path / "classifications.json")
    assert c.get("Nowhere", "Nothing") is None


def test_save_and_reload_persists(tmp_path):
    path = tmp_path / "classifications.json"
    c = Cache(path)
    c.put(Classification(
        title="Poetry Night", source="Bird & Beckett",
        types=[["social"]], topics=["poetry"], cost="free",
        model="m", taxonomy_version=1, classified_at="2026-09-23T00:00:00+00:00",
    ))
    c.save()
    # fresh instance reads what was saved
    c2 = Cache(path)
    got = c2.get("Bird & Beckett", "Poetry Night")
    assert got is not None
    assert got.topics == ["poetry"]


def test_saved_file_is_stably_sorted(tmp_path):
    """Byte-identical re-save (sorted keys) so committed diffs stay clean."""
    path = tmp_path / "classifications.json"
    c = Cache(path)
    for src, title in [("Z", "z-show"), ("A", "a-show"), ("M", "m-show")]:
        c.put(Classification(
            title=title, source=src, types=[["talk"]], topics=[], cost="unknown",
            model="m", taxonomy_version=1, classified_at="2026-09-23T00:00:00+00:00",
        ))
    c.save()
    first = path.read_text()
    Cache(path).save()  # reload + re-save
    assert path.read_text() == first
    # keys are sorted
    data = json.loads(first)
    assert list(data["entries"].keys()) == sorted(data["entries"].keys())


def test_missing_file_loads_as_empty(tmp_path):
    c = Cache(tmp_path / "does-not-exist.json")
    assert c.get("x", "y") is None
    # saving creates the file
    c.save()
    assert (tmp_path / "does-not-exist.json").exists()
