"""Cross-source fuzzy duplicate detection.

Two sources often list one physical event under different titles — a
bookstore and Litquake, a venue and SF Bar Guide, a venue and its forwarded
newsletter ("Community Co-Working" vs "Alembic Community Co-Working").
`main._find_duplicate` uses this only after exact matches fail, only for
events at the *same start time*, and only against rows from a *different*
source (one source listing "Beginner Zouk" and "Intermediate Zouk" at 7pm
means two events).

Rules, tuned on production (2026-09-29: 29 real cross-source pairs, no false
positives in the sample):
- Titles are normalized (accents, curly quotes, case, punctuation) and filler
  words dropped.
- Locations, when both are known, must agree. When both strings map to
  venues in the committed venue files (feature-specs/venues.md), they agree
  iff the venue is the same (and the room, when both name one); otherwise a
  shared street number or one venue name inside the other. Disagreeing
  locations never match.
- With agreeing locations, titles match on containment (the smaller title's
  words — at least 2 — all appear in the larger) or ≥50% word overlap.
- With a location missing on either side, only containment counts.
"""
from __future__ import annotations

import re
import unicodedata

_STOP = {"the", "a", "an", "with", "at", "and", "of", "in", "on", "for", "presents", "presented",
         "by", "featuring", "feat", "ft", "to", "live", "night", "show", "offsite"}
# A street number: digits followed by a street name, which may itself start
# with a digit ("565 Green", "2781 24th Street"). ZIP codes ("CA 94110") are
# excluded by not following a two-letter state code.
_STREET_NUMBER_RE = re.compile(r"(?<![A-Z]{2} )(?<![A-Z]{2}, )\b(\d{1,5})\s+(?=\d{0,3}[A-Za-z])")


def _normalize(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def venue_lookup(store):
    """A `venue_of` for locations_agree from a places Store; None if the
    files are invalid (then only the text rules apply)."""
    if store is None or store.validate():
        return None

    def venue_of(location):
        e = store.entry(location)
        return (e["venue"], e.get("room")) if e and "venue" in e else None
    return venue_of


def title_tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", _normalize(title or "")) if len(w) > 1 and w not in _STOP}


def _contained(a: set[str], b: set[str]) -> bool:
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return len(small) >= 2 and small <= big


def locations_agree(a: str | None, b: str | None, venue_of=None) -> bool | None:
    """True/False when both are known; None when either is missing.

    `venue_of(location) -> (venue_id, room | None) | None` looks a string up
    in the venue files (known entries only, no lookups)."""
    if not (a and a.strip()) or not (b and b.strip()):
        return None
    va, vb = (venue_of(a), venue_of(b)) if venue_of else (None, None)
    if va and vb:
        if va[0] != vb[0]:
            return False
        return not (va[1] and vb[1] and va[1].casefold() != vb[1].casefold())
    if set(_STREET_NUMBER_RE.findall(a)) & set(_STREET_NUMBER_RE.findall(b)):
        return True
    na, nb = _normalize(a), _normalize(b)
    venue_a, venue_b = na.split(",")[0].strip(), nb.split(",")[0].strip()
    return bool(venue_a and venue_a in nb) or bool(venue_b and venue_b in na)


def is_near_duplicate(title_a: str, location_a: str | None,
                      title_b: str, location_b: str | None, venue_of=None) -> bool:
    ta, tb = title_tokens(title_a), title_tokens(title_b)
    if not ta or not tb:
        return False
    agree = locations_agree(location_a, location_b, venue_of)
    if agree is False:
        return False
    if _contained(ta, tb):
        return True
    return agree is True and len(ta & tb) / len(ta | tb) >= 0.5
