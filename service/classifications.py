"""Committed classification cache — durable, portable, git-diffable.

Tags live in `data/classifications.json`, NOT Postgres: the DB is an ephemeral
local working store (wiped with `docker compose down -v`, empty in CI), but a
cache that costs LLM money to fill must outlive DB wipes and travel in git,
exactly like `frontend/events.json`. "Classify once, cache forever" then holds.

Keyed per *show* (`source` + `title`), not per performance — a show playing 20
nights is one classification, and the exporter joins each event to its show's
entry at export time.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# US unit separator — joins source+title into one unambiguous key.
_SEP = "\x1f"


def show_key(source: str, title: str) -> str:
    return f"{source}{_SEP}{title}"


@dataclass
class Classification:
    title: str
    source: str
    types: list[list[str]]
    topics: list[str]
    cost: str
    model: str
    taxonomy_version: int
    classified_at: str


class Cache:
    """Load/query/update the classifications file. `save()` writes pretty,
    sorted JSON so re-saving unchanged data is byte-identical (clean diffs)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._entries: dict[str, Classification] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text())
        for key, raw in (data.get("entries") or {}).items():
            self._entries[key] = Classification(
                title=raw["title"],
                source=raw["source"],
                types=[list(p) for p in raw.get("types", [])],
                topics=list(raw.get("topics", [])),
                cost=raw.get("cost", "unknown"),
                model=raw.get("model", ""),
                taxonomy_version=raw.get("taxonomy_version", 0),
                classified_at=raw.get("classified_at", ""),
            )

    def get(self, source: str, title: str) -> Classification | None:
        return self._entries.get(show_key(source, title))

    def put(self, entry: Classification) -> None:
        self._entries[show_key(entry.source, entry.title)] = entry

    def __contains__(self, key: str) -> bool:
        return key in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "entries": {k: asdict(self._entries[k]) for k in sorted(self._entries)},
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        )
