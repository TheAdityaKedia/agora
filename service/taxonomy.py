"""Two-axis event taxonomy: the single source of truth for tags.

The taxonomy has two independent axes (see feature-specs/tagging.md):

  - `type`  — the event FORMAT (performance, screening, talk/reading, workshop,
              exhibition, social/book-club, …). A shallow tree; a classification
              is a *path* that may stop at any level.
  - `topic` — what the event is ABOUT (poetry, jazz, theater, women, …). A flat,
              curated, multi-select vocabulary of slugs. Cuts across formats, so
              a "poetry" filter catches a reading, an open mic, and a workshop.

Versioned JSON files (`data/taxonomy.v{N}.json`) keep published versions
immutable so old classifications stay interpretable. This module is the only
place that reads them.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CURRENT_TAXONOMY_VERSION = 1
_DATA_DIR = Path(__file__).parent / "data"


@lru_cache(maxsize=None)
def load_taxonomy(version: int = CURRENT_TAXONOMY_VERSION) -> dict:
    """Load and return the taxonomy JSON for `version`."""
    path = _DATA_DIR / f"taxonomy.v{version}.json"
    tax = json.loads(path.read_text())
    if tax.get("version") != version:
        raise ValueError(
            f"{path.name} declares version {tax.get('version')}, expected {version}"
        )
    return tax


def _type_tree(version: int) -> dict:
    return load_taxonomy(version)["axes"]["type"]["tree"]


@lru_cache(maxsize=None)
def type_valid_paths(version: int = CURRENT_TAXONOMY_VERSION) -> frozenset[tuple[str, ...]]:
    """Every valid type path, including partial paths (a path may stop at any
    level: ("talk",) and ("talk","reading") are both valid).
    """
    acc: set[tuple[str, ...]] = set()

    def walk(node: dict, prefix: tuple[str, ...]) -> None:
        for slug, child in node.items():
            path = prefix + (slug,)
            acc.add(path)
            kids = child.get("children")
            if kids:
                walk(kids, path)

    walk(_type_tree(version), ())
    return frozenset(acc)


def validate_type(path, version: int = CURRENT_TAXONOMY_VERSION) -> list[str] | None:
    """Return the longest valid prefix of `path`, or None if even the top level
    is unknown. Coerces a slightly-off classification (unknown leaf → its valid
    parent) and is defensive about odd shapes (bare string, non-strings, nesting).
    """
    if isinstance(path, str):
        path = [path]
    if not isinstance(path, (list, tuple)):
        return None
    flat = [p for p in path if isinstance(p, str)]
    valid = type_valid_paths(version)
    t = tuple(flat)
    while t:
        if t in valid:
            return list(t)
        t = t[:-1]
    return None


@lru_cache(maxsize=None)
def valid_topics(version: int = CURRENT_TAXONOMY_VERSION) -> frozenset[str]:
    """The flat set of valid topic slugs."""
    return frozenset(load_taxonomy(version)["axes"]["topic"]["values"].keys())


def validate_topics(topics, version: int = CURRENT_TAXONOMY_VERSION) -> list[str]:
    """Keep only known topic slugs, in first-seen order, deduped. Defensive
    about bare strings, nesting, and None (models drift)."""
    valid = valid_topics(version)

    def flatten(x):
        if isinstance(x, str):
            yield x
        elif isinstance(x, (list, tuple)):
            for y in x:
                yield from flatten(y)

    seen: set[str] = set()
    out: list[str] = []
    for slug in flatten(topics):
        if slug in valid and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def render_for_prompt(version: int = CURRENT_TAXONOMY_VERSION) -> str:
    """Render both axes as text for the classifier prompt: the type tree
    (indented) and the topic vocabulary (grouped by its `group`)."""
    tax = load_taxonomy(version)

    type_lines: list[str] = []

    def walk(node: dict, depth: int) -> None:
        for slug, child in node.items():
            type_lines.append("  " * depth + f"- {slug}: {child['label']}")
            kids = child.get("children")
            if kids:
                walk(kids, depth + 1)

    walk(tax["axes"]["type"]["tree"], 0)

    topics = tax["axes"]["topic"]["values"]
    by_group: dict[str, list[str]] = {}
    for slug, meta in topics.items():
        by_group.setdefault(meta["group"], []).append(slug)
    topic_lines = [
        f"  {group}: " + ", ".join(slugs) for group, slugs in by_group.items()
    ]

    return (
        "TYPE options (format):\n"
        + "\n".join(type_lines)
        + "\n\nTOPIC options (subject) — choose slugs only from here:\n"
        + "\n".join(topic_lines)
    )
