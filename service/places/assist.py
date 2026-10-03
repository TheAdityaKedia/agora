"""AI-assisted resolution (feature-specs/venues.md → "Phase 3 design").

For a location string the rule-based steps can't place, the model proposes
what it names: a venue's name, street address and city. The proposal only
adds map queries; whatever it finds must still pass the evidence rules
against the source's own text (resolve.py). The model never supplies
coordinates, and a proposal alone never makes a venue.
"""
from __future__ import annotations

import json
import re

# Model calls per run; the rest wait (pending strings retry weekly).
MAX_CALLS = 20
KINDS = ("venue", "not_a_place", "unknown")

_SYSTEM = (
    "You identify where an event in the San Francisco Bay Area takes place. "
    "Given the location text an event listing used, and who listed it, say what "
    "place it names. Reply with JSON only, no prose:\n"
    '{"kind": "venue" | "not_a_place" | "unknown", "name": str, '
    '"street_address": str, "city": str}\n'
    "- venue: one physical place. name = its current proper name; street_address "
    "= house number and street if you know them; city = its city.\n"
    "- not_a_place: no single place (TBA, several locations, a whole city, online).\n"
    "- unknown: you can't tell. Leave fields you don't know as \"\". Never guess "
    "an address: an empty street_address is better than a wrong one."
)


def build_prompt(location: str, sources: list[str], profiles: dict[str, str]) -> tuple[str, str]:
    lines = [f"Location text: {location}"]
    for s in sources[:3]:
        about = profiles.get(s)
        lines.append(f"Listed by: {s}" + (f" — {about}" if about else ""))
    return _SYSTEM, "\n".join(lines)


def parse_proposal(text: str) -> dict | None:
    """The model's JSON → {kind, name, street_address, city}, or None."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        raw = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        return None
    out = {"kind": raw["kind"]}
    for k in ("name", "street_address", "city"):
        v = raw.get(k)
        out[k] = v.strip() if isinstance(v, str) else ""
    return out


class Assistant:
    """Asks the model about one string at a time, within a per-run budget.

    `ask(system, user) -> str` is the model call (main.py wires Bedrock);
    `profiles` maps a source name to its source_profiles.json line.
    """

    def __init__(self, ask, profiles: dict[str, str] | None = None, max_calls: int = MAX_CALLS):
        self.ask = ask
        self.profiles = profiles or {}
        self.max_calls = max_calls
        self.calls = 0
        self.errors = 0

    def propose(self, location: str, sources: list[str]) -> dict | None:
        """A proposal, or None (over budget, model error, unparseable)."""
        if self.calls >= self.max_calls or self.errors >= 3:
            return None  # over budget, or the model is unreachable this run
        self.calls += 1
        try:
            return parse_proposal(self.ask(*build_prompt(location, sources, self.profiles)))
        except Exception:
            self.errors += 1
            return None


def queries(p: dict) -> list[str]:
    """Map queries a venue proposal adds: by name, then by address."""
    if p.get("kind") != "venue":
        return []
    city = p.get("city") or ""
    out = []
    for part in (p.get("name"), p.get("street_address")):
        if part:
            out.append(f"{part}, {city}" if city and city.lower() not in part.lower() else part)
    return list(dict.fromkeys(out))


def summary(p: dict) -> str:
    """One line for evidence and the review issue."""
    if p.get("kind") != "venue":
        return p.get("kind", "unknown").replace("_", " ")
    return ", ".join(x for x in (p.get("name"), p.get("street_address"), p.get("city")) if x)
