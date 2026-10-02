"""Text normalisation for location strings and venue names."""
from __future__ import annotations

import re
import unicodedata

_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "—"})
_TRAILING = re.compile(r"(,?\s*(usa|united states|us))?(,?\s*\d{5}(-\d{4})?)?[\s,.;]*$")


def normalize_key(text: str | None) -> str:
    """The key a location string is stored under in venue_locations.json.

    NFKC, straight quotes, lowercase, collapsed whitespace, and a trailing
    ", USA" / ZIP dropped — so "X, CA 94110, USA" and "X, CA" share a key.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text).translate(_QUOTES).lower()
    t = re.sub(r"\s+", " ", t).strip()
    prev = None
    while prev != t:  # "…, CA 94110, USA" needs two passes
        prev, t = t, _TRAILING.sub("", t).strip()
    return t


# Words that say what kind of place something is but not which one: a match
# on these alone ("Library" ~ "Library") proves nothing.
GENERIC = {
    "library", "branch", "theater", "center", "hall", "bar", "cafe", "club",
    "books", "bookstore", "bookshop", "studio", "studios", "gallery", "park",
    "church", "museum", "room", "lounge", "stage", "house", "space", "main",
    "public", "store", "shop", "restaurant", "auditorium", "san", "francisco",
    "sf", "oakland", "berkeley", "ca", "street", "st", "ave", "avenue",
}
_STOP = {"the", "at", "of", "and", "a", "an", "on", "in", "s"}
_SPELLING = {"theatre": "theater", "centre": "center", "bookshop": "books"}


def name_tokens(text: str | None) -> set[str]:
    t = unicodedata.normalize("NFKD", (text or "").translate(_QUOTES))
    t = t.encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", t)
    return {_SPELLING.get(w, w) for w in words if w not in _STOP}


def names_match(a: str | None, b: str | None) -> bool:
    """Do two venue names plausibly name the same place?

    True for identical token sets; for containment when the shorter name has a
    distinctive (non-generic) word and the longer adds only generic ones (or
    the shorter has two distinctive words); or for a strong overlap.
    """
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    distinctive = small - GENERIC
    if small <= big and distinctive:
        # "Specs'" ~ "Specs Bar" (the extra word is generic), but not
        # "The Chapel" ~ "Chapel of Grace" (a distinctive extra word means a
        # different place) — unless the shorter name is itself specific.
        if not (big - small) - GENERIC or len(distinctive) >= 2:
            return True
        # "Herbst Theatre" ~ "War Memorial Veterans Building Herbst Theatre":
        # the longer name ends with the shorter one, two words or more.
        wa, wb = _words(a), _words(b)
        short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
        if len(short) >= 2 and long_[-len(short):] == short:
            return True
    shared = ta & tb
    return bool(shared - GENERIC) and len(shared) / len(ta | tb) >= 0.6


def _words(text: str | None) -> list[str]:
    t = unicodedata.normalize("NFKD", (text or "").translate(_QUOTES))
    t = t.encode("ascii", "ignore").decode().lower()
    return [_SPELLING.get(w, w) for w in re.findall(r"[a-z0-9]+", t) if w not in _STOP]


_ROOM_SPLIT = re.compile(r"\s+(?:—|-{1,2}|@|·)\s+")


def venue_part(text: str) -> tuple[str, str | None]:
    """("SFJAZZ Center — Miner Auditorium, SF") → ("SFJAZZ Center", "Miner Auditorium").

    The venue is the first comma-separated segment; a room after a dash, "@"
    or middle dot, or in trailing parentheses, is split off.
    """
    first = text.split(",")[0].strip()
    parts = _ROOM_SPLIT.split(first, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0].strip(), parts[1].strip()
    # "The Berkeley Alembic (Luna)" — a room in parentheses.
    m = re.match(r"^(.+?)\s*\(([^)]+)\)$", first)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return first, None
