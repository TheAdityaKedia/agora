"""Nominatim (OpenStreetMap) geocoding client.

Usage policy (https://operations.osmfoundation.org/policies/nominatim/): at
most one request per second, an identifying User-Agent, cache results, no bulk
jobs. Searches are bounded to the Bay Area. OpenStreetMap data is ODbL —
credit "© OpenStreetMap contributors" wherever addresses or maps are shown.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "Agora-event-aggregator/1.0 (+https://github.com/TheAdityaKedia/agora)"
# lon_min, lat_max, lon_max, lat_min — a box around the nine counties.
BAY_AREA_VIEWBOX = "-123.65,38.87,-121.20,36.89"


_POI_CATEGORIES = {"amenity", "leisure", "tourism", "shop", "historic", "building",
                   "office", "club", "craft"}


@dataclass
class Place:
    """One Nominatim result, reduced to what resolution needs."""
    lat: float
    lng: float
    name: str
    osm: str                     # "node/123", "way/456"
    category: str
    type: str
    place_rank: int
    address: dict = field(default_factory=dict)
    display_name: str = ""

    @classmethod
    def from_json(cls, r: dict) -> "Place":
        return cls(
            lat=float(r["lat"]), lng=float(r["lon"]), name=r.get("name") or "",
            osm=f"{r.get('osm_type', '')}/{r.get('osm_id', '')}",
            category=r.get("category", ""), type=r.get("type", ""),
            place_rank=int(r.get("place_rank", 0)), address=r.get("address") or {},
            display_name=r.get("display_name", ""),
        )

    @property
    def precision(self) -> str:
        """building (a POI or house number) · street · city (area centroid)."""
        if self.place_rank >= 28 or self.address.get("house_number"):
            return "building"
        # A named park, museum, bar…: OSM ranks parks below buildings, but
        # the point is the place itself, not an area around it.
        if self.name and self.place_rank >= 22 and self.category in _POI_CATEGORIES:
            return "building"
        if self.place_rank >= 26:
            return "street"
        return "city"

    @property
    def is_poi(self) -> bool:
        return bool(self.name) and self.precision == "building" and self.category not in ("place", "boundary", "highway", "landuse")

    def street_address(self) -> str:
        a = self.address
        street = " ".join(x for x in (a.get("house_number"), a.get("road")) if x)
        city = a.get("city") or a.get("town") or a.get("village") or a.get("hamlet") or ""
        tail = " ".join(x for x in ("CA", a.get("postcode")) if x)
        return ", ".join(x for x in (street, city, tail) if x)


class Nominatim:
    """Rate-limited Nominatim search with an optional on-disk response cache.

    `cache_path` is a dev convenience for iterating on the bootstrap without
    re-querying; it is not committed. `fetch` replaces the HTTP call in tests.
    """

    def __init__(self, user_agent: str = USER_AGENT, min_interval: float = 1.1,
                 cache_path: Path | None = None, fetch=None, max_calls: int | None = None):
        self.user_agent = user_agent
        self.min_interval = min_interval
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, list] = {}
        if self.cache_path and self.cache_path.exists():
            self._cache = json.loads(self.cache_path.read_text())
        self._fetch = fetch or self._http
        self._last = 0.0
        self.calls = 0
        self.max_calls = max_calls

    @property
    def exhausted(self) -> bool:
        return self.max_calls is not None and self.calls >= self.max_calls

    def _http(self, query: str) -> list:
        wait = self._last + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        resp = requests.get(URL, params={
            "q": query, "format": "jsonv2", "addressdetails": 1, "limit": 3,
            "viewbox": BAY_AREA_VIEWBOX, "bounded": 1, "countrycodes": "us",
        }, headers={"User-Agent": self.user_agent}, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def search(self, query: str) -> list[Place]:
        if query not in self._cache:
            if self.exhausted:
                raise BudgetExhausted(query)
            self.calls += 1
            self._cache[query] = self._fetch(query)
            if self.cache_path:
                self.cache_path.write_text(json.dumps(self._cache, indent=1, sort_keys=True))
        return [Place.from_json(r) for r in self._cache[query]]


class BudgetExhausted(Exception):
    """The per-run lookup cap was reached; the string rolls to the next run."""
