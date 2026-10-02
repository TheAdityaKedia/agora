"""The committed venue files: load, validate, update, save.

- data/venues.json — the registry: venues by id, plus `source_homes` (the
  region a mostly-single-venue source's events are in; a weak signal).
- data/venue_locations.json — normalised location string → what it is:
  {"venue": id, "room"?}, {"place": "online" | "none" | "outside"},
  {"place": "region", "region": id}, or {"pending": {...}}.

Both are written pretty and key-sorted so an unchanged re-save is
byte-identical (clean diffs in the data PRs).
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import date
from pathlib import Path

from .normalize import normalize_key
from .regions import REGIONS, region_at

DATA_DIR = Path(__file__).parent.parent / "data"
VENUES_FILE = "venues.json"
LOCATIONS_FILE = "venue_locations.json"

PHYSICAL_REGIONS = {r for r in REGIONS if r != "online"}
PRECISIONS = {"building", "street", "city"}
STATUSES = {"verified", "auto"}
_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_VENUES_NOTE = (
    "Venue registry (feature-specs/venues.md). Edit by hand to fix a venue; "
    "`python -m places validate` checks it. status: verified = a person checked "
    "it, auto = passed the evidence rules unattended. source_homes: the region "
    "a mostly-single-venue source's events are in (a weak tiebreaker signal)."
)
_LOCATIONS_NOTE = (
    "Normalised location string -> venue / place. To fix a pending string, "
    "replace its entry with {\"venue\": \"<id>\"} (optionally \"room\"), "
    "{\"place\": \"none\"} or {\"place\": \"online\"}."
)


def meters_between(lat1, lng1, lat2, lng2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def slugify(name: str) -> str:
    t = unicodedata.normalize("NFKD", normalize_key(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.replace("'", "")).strip("-") or "venue"


class Store:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.dir = Path(data_dir)
        v = self._read(VENUES_FILE)
        self.venues: dict[str, dict] = v.get("venues", {})
        self.source_homes: dict[str, str] = v.get("source_homes", {})
        self.locations: dict[str, dict] = self._read(LOCATIONS_FILE).get("locations", {})

    def _read(self, name: str) -> dict:
        p = self.dir / name
        return json.loads(p.read_text()) if p.exists() else {}

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for name, payload in (
            (VENUES_FILE, {"version": 1, "note": _VENUES_NOTE,
                           "source_homes": self.source_homes, "venues": self.venues}),
            (LOCATIONS_FILE, {"version": 1, "note": _LOCATIONS_NOTE,
                              "locations": self.locations}),
        ):
            (self.dir / name).write_text(
                json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

    # --- lookups ---------------------------------------------------------

    def entry(self, location: str | None) -> dict | None:
        return self.locations.get(normalize_key(location))

    def region_of(self, location: str | None) -> str | None:
        """The region an event at `location` is in, or None (unknown)."""
        e = self.entry(location)
        if not e:
            return None
        if "venue" in e:
            return self.venues.get(e["venue"], {}).get("region")
        if e.get("place") == "online":
            return "online"
        if e.get("place") == "region":
            return e.get("region")
        return None

    def near(self, lat: float, lng: float, meters: float) -> list[tuple[float, str]]:
        """(distance, venue id) for venues with coordinates within `meters`."""
        out = []
        for vid, v in self.venues.items():
            if v.get("lat") is not None:
                d = meters_between(lat, lng, v["lat"], v["lng"])
                if d <= meters:
                    out.append((d, vid))
        return sorted(out)

    def find_by_osm(self, osm: str) -> str | None:
        return next((vid for vid, v in self.venues.items() if osm and v.get("osm") == osm), None)

    # --- updates ---------------------------------------------------------

    def add_venue(self, fields: dict) -> str:
        base = slugify(fields["name"])
        if base == "venue" and fields.get("address"):
            base = slugify(fields["address"])
        vid, n = base, 2
        while vid in self.venues:
            vid, n = f"{base}-{n}", n + 1
        self.venues[vid] = {"added": date.today().isoformat(), **fields}
        return vid

    def set_location(self, location: str, entry: dict) -> None:
        self.locations[normalize_key(location)] = entry

    # --- validation ------------------------------------------------------

    def validate(self) -> list[str]:
        """Problems that would make areas wrong or crash the export."""
        errs = []
        for src, region in self.source_homes.items():
            if region not in PHYSICAL_REGIONS:
                errs.append(f"source_homes[{src!r}]: unknown region {region!r}")
        for vid, v in self.venues.items():
            where = f"venues[{vid!r}]"
            if not _ID.match(vid):
                errs.append(f"{where}: id must be a lowercase slug")
            if not v.get("name"):
                errs.append(f"{where}: missing name")
            if v.get("region") not in PHYSICAL_REGIONS:
                errs.append(f"{where}: unknown region {v.get('region')!r}")
            if v.get("status") not in STATUSES:
                errs.append(f"{where}: status must be one of {sorted(STATUSES)}")
            if v.get("precision") not in PRECISIONS:
                errs.append(f"{where}: precision must be one of {sorted(PRECISIONS)}")
            has_lat, has_lng = v.get("lat") is not None, v.get("lng") is not None
            if has_lat != has_lng:
                errs.append(f"{where}: lat and lng go together")
            elif has_lat:
                at = region_at(v["lat"], v["lng"])
                if at is None:
                    errs.append(f"{where}: coordinates are outside the Bay Area counties")
                elif at != v.get("region"):
                    errs.append(f"{where}: coordinates are in {at}, but region says {v.get('region')}")
        for key, e in self.locations.items():
            where = f"locations[{key!r}]"
            if key != normalize_key(key):
                errs.append(f"{where}: key is not normalised (expected {normalize_key(key)!r})")
            kinds = [k for k in ("venue", "place", "pending") if k in e]
            if len(kinds) != 1:
                errs.append(f"{where}: needs exactly one of venue / place / pending")
                continue
            if "venue" in e and e["venue"] not in self.venues:
                errs.append(f"{where}: unknown venue {e['venue']!r}")
            if "place" in e:
                if e["place"] not in ("online", "none", "region", "outside"):
                    errs.append(f"{where}: place must be online, none, region or outside")
                elif e["place"] == "region" and e.get("region") not in PHYSICAL_REGIONS:
                    errs.append(f"{where}: unknown region {e.get('region')!r}")
            if "pending" in e and not (e["pending"] or {}).get("reason"):
                errs.append(f"{where}: pending needs a reason")
        return errs
