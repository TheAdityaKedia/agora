"""Tests for the two-axis taxonomy loader (service/taxonomy.py)."""
import taxonomy


def test_load_returns_current_version():
    tax = taxonomy.load_taxonomy()
    assert tax["version"] == taxonomy.CURRENT_TAXONOMY_VERSION


def test_type_valid_paths_includes_partial_and_full():
    paths = taxonomy.type_valid_paths()
    # top-level, and a real sub-format
    assert ("performance",) in paths
    assert ("talk",) in paths
    assert ("talk", "reading") in paths
    assert ("social", "book-club") in paths
    # a genre that was moved OUT of type into topic must NOT be a type path
    assert ("performance", "music", "jazz") not in paths


def test_validate_type_coerces_unknown_leaf_to_parent():
    # unknown leaf under a valid top → longest valid prefix
    assert taxonomy.validate_type(["talk", "bogus"]) == ["talk"]
    # fully valid path passes through
    assert taxonomy.validate_type(["social", "book-club"]) == ["social", "book-club"]
    # unknown top-level → None
    assert taxonomy.validate_type(["nonsense"]) is None
    # empty / junk → None
    assert taxonomy.validate_type([]) is None


def test_valid_topics_is_flat_set_of_slugs():
    topics = taxonomy.valid_topics()
    assert "poetry" in topics
    assert "jazz" in topics
    assert "women" in topics
    assert "writing" in topics
    # not a path — a plain slug set
    assert all(isinstance(t, str) for t in topics)


def test_validate_topics_keeps_known_drops_unknown_and_dedupes():
    got = taxonomy.validate_topics(["poetry", "bogus", "jazz", "poetry"])
    # known kept in order, unknown dropped, deduped
    assert got == ["poetry", "jazz"]


def test_validate_topics_handles_non_list_and_nested():
    # models sometimes return a bare string or nested lists
    assert taxonomy.validate_topics("poetry") == ["poetry"]
    assert taxonomy.validate_topics([["poetry"], "jazz"]) == ["poetry", "jazz"]
    assert taxonomy.validate_topics(None) == []


def test_render_for_prompt_contains_both_axes():
    text = taxonomy.render_for_prompt()
    # type formats present
    assert "performance" in text
    assert "reading" in text
    # topic slugs present, grouped
    assert "poetry" in text
    assert "jazz" in text
