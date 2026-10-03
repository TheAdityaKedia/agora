"""Bay Area regions: county shapes, point-in-polygon, and city names.

Shapes: data/geo/bay_area_counties.geojson, from US Census TIGERweb
(public domain), regenerated with:

  curl -G https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query \\
    --data-urlencode "where=STATE='06' AND COUNTY IN ('001','013','041','055','075','081','085','095','097')" \\
    --data-urlencode outFields=NAME --data-urlencode f=geojson --data-urlencode outSR=4326 \\
    --data-urlencode geometryPrecision=5 --data-urlencode maxAllowableOffset=0.0003

County polygons include their water, so a pier or island venue still lands
in a county.

SF neighbourhoods: data/geo/sf_neighborhoods.geojson, DataSF "Analysis
Neighborhoods" (dataset j2bu-swwd, PDDL), regenerated with:

  curl -LG https://data.sf.gov/resource/j2bu-swwd.geojson --data-urlencode '$limit=100' \\
    --data-urlencode '$select=nhood, simplify_preserve_topology(the_geom, 0.0001) as the_geom'

then coordinates rounded to 5 decimals and each feature given an `id` (a
slug of `nhood`; South of Market is `soma`) and a `label` (slashes spaced;
"SoMa"). One feature per line. See feature-specs/venues.md, phase 4.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .normalize import normalize_key

GEO_FILE = Path(__file__).parent.parent / "data" / "geo" / "bay_area_counties.geojson"
NEIGHBORHOODS_FILE = Path(__file__).parent.parent / "data" / "geo" / "sf_neighborhoods.geojson"

# id → label, in display order. "online" has no counties.
REGIONS = {
    "sf": "San Francisco",
    "eastbay": "East Bay",
    "peninsula": "Peninsula",
    "southbay": "South Bay",
    "northbay": "North Bay",
    "online": "Online",
}
REGION_OF_COUNTY = {
    "San Francisco": "sf",
    "Alameda": "eastbay", "Contra Costa": "eastbay",
    "San Mateo": "peninsula",
    "Santa Clara": "southbay",
    "Marin": "northbay", "Sonoma": "northbay", "Napa": "northbay", "Solano": "northbay",
}

# Bay Area cities and well-known unincorporated towns → county. Used to read
# the city out of an address ("…, Berkeley, CA 94704").
CITY_COUNTY = {
    "San Francisco": ["San Francisco", "SF"],
    "Alameda": ["Alameda", "Albany", "Berkeley", "Castro Valley", "Dublin", "Emeryville",
                "Fremont", "Hayward", "Livermore", "Newark", "Oakland", "Piedmont",
                "Pleasanton", "San Leandro", "San Lorenzo", "Sunol", "Union City"],
    "Contra Costa": ["Alamo", "Antioch", "Brentwood", "Clayton", "Concord", "Crockett",
                     "Danville", "El Cerrito", "El Sobrante", "Hercules", "Kensington",
                     "Lafayette", "Martinez", "Moraga", "Oakley", "Orinda", "Pinole",
                     "Pittsburg", "Pleasant Hill", "Richmond", "Rodeo", "San Pablo",
                     "San Ramon", "Walnut Creek"],
    "San Mateo": ["Atherton", "Belmont", "Brisbane", "Burlingame", "Colma", "Daly City",
                  "East Palo Alto", "El Granada", "Foster City", "Half Moon Bay",
                  "Hillsborough", "Menlo Park", "Millbrae", "Montara", "Moss Beach",
                  "Pacifica", "Pescadero", "Portola Valley", "Redwood City", "San Bruno",
                  "San Carlos", "San Mateo", "South San Francisco", "Woodside"],
    "Santa Clara": ["Campbell", "Cupertino", "Gilroy", "Los Altos", "Los Altos Hills",
                    "Los Gatos", "Milpitas", "Monte Sereno", "Morgan Hill", "Mountain View",
                    "Palo Alto", "San Jose", "Santa Clara", "Saratoga", "Stanford", "Sunnyvale"],
    "Marin": ["Belvedere", "Bolinas", "Corte Madera", "Fairfax", "Greenbrae", "Inverness",
              "Kentfield", "Larkspur", "Mill Valley", "Novato", "Point Reyes Station", "Ross",
              "San Anselmo", "San Rafael", "Sausalito", "Stinson Beach", "Tiburon"],
    "Sonoma": ["Bodega Bay", "Cloverdale", "Cotati", "Glen Ellen", "Guerneville",
               "Healdsburg", "Petaluma", "Rohnert Park", "Santa Rosa", "Sebastopol",
               "Sonoma", "Windsor"],
    "Napa": ["American Canyon", "Calistoga", "Napa", "St. Helena", "Yountville"],
    "Solano": ["Benicia", "Dixon", "Fairfield", "Rio Vista", "Suisun City", "Vacaville", "Vallejo"],
}
_CITY_TO_COUNTY = {normalize_key(c): county for county, cities in CITY_COUNTY.items() for c in cities}
_CITIES_LONGEST_FIRST = sorted(_CITY_TO_COUNTY, key=len, reverse=True)
_STATE = re.compile(r"\b(ca|calif|california)\b\.?|\b\d{5}(-\d{4})?\b|\busa\b")


def _clean_segment(seg: str) -> str:
    return re.sub(r"\s+", " ", _STATE.sub(" ", normalize_key(seg))).strip(" .")


def city_in_text(text: str | None) -> tuple[str, str] | None:
    """The Bay Area city an address names, as (city key, county), or None.

    Only a whole comma-separated segment counts ("…, Berkeley, CA"), so a
    street named after a city ("Vallejo St") or a venue that starts with one
    ("Oakland Art Murmur") isn't mistaken for the address city. The last
    matching segment wins — the city comes after the street.
    """
    found = None
    for seg in (text or "").split(","):
        key = _clean_segment(seg)
        if key in _CITY_TO_COUNTY:
            found = (key, _CITY_TO_COUNTY[key])
        elif re.search(r"\d", key):
            # An address line with the city run on: "51 Tamal Vista Blvd Corte Madera".
            for city in _CITIES_LONGEST_FIRST:
                if key.endswith(" " + city):
                    found = (city, _CITY_TO_COUNTY[city])
                    break
    return found


def city_segment(text: str | None) -> str | None:
    """The segment just before the state ("…, Santa Cruz, CA" → "santa cruz"),
    whether or not it's a Bay Area city."""
    segs = [s.strip() for s in normalize_key(text).split(",")]
    for i in range(len(segs) - 1, 0, -1):
        if re.fullmatch(r"(ca|calif|california)(\s+\d{5})?", segs[i]):
            prev = _clean_segment(segs[i - 1])
            return prev if prev and not re.search(r"\d", prev) else None
    return None


def whole_string_city(text: str | None) -> tuple[str, str] | None:
    """When the entire location is just a city ("San Francisco, CA")."""
    segs = [s for s in (_clean_segment(s) for s in (text or "").split(",")) if s]
    if segs and all(s in _CITY_TO_COUNTY for s in segs):
        counties = {_CITY_TO_COUNTY[s] for s in segs}
        if len(counties) == 1:
            return segs[-1], counties.pop()
    return None


@lru_cache(maxsize=1)
def _counties(path: str = str(GEO_FILE)) -> list[tuple[str, list]]:
    data = json.loads(Path(path).read_text())
    out = []
    for f in data["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        out.append((f["properties"]["county"], polys))
    return out


@lru_cache(maxsize=1)
def _neighborhoods(path: str = str(NEIGHBORHOODS_FILE)) -> list[tuple[str, str, list]]:
    data = json.loads(Path(path).read_text())
    return [(f["properties"]["id"], f["properties"]["label"], f["geometry"]["coordinates"])
            for f in data["features"]]


def neighborhoods() -> dict[str, str]:
    """SF neighbourhood id → label, sorted by label."""
    return dict(sorted(((nid, label) for nid, label, _ in _neighborhoods()), key=lambda x: x[1]))


def _in_polys(lng: float, lat: float, polys: list) -> bool:
    return any(_in_ring(lng, lat, poly[0]) and not any(_in_ring(lng, lat, h) for h in poly[1:])
               for poly in polys)


def _in_ring(lng: float, lat: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def county_at(lat: float, lng: float) -> str | None:
    """The Bay Area county containing the point, or None outside the nine."""
    for county, polys in _counties():
        if _in_polys(lng, lat, polys):
            return county
    return None


def region_at(lat: float, lng: float) -> str | None:
    county = county_at(lat, lng)
    return REGION_OF_COUNTY.get(county) if county else None


def neighborhood_at(lat: float, lng: float) -> str | None:
    """The SF neighbourhood id containing the point, or None (outside SF,
    or in the bay between the shapes)."""
    for nid, _label, polys in _neighborhoods():
        if _in_polys(lng, lat, polys):
            return nid
    return None
