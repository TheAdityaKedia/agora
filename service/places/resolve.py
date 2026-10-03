"""Resolve a location string to a venue, a region, or the review queue.

Steps, in order (feature-specs/venues.md → "Resolving a new location string"):

1. Known string — already in venue_locations.json (and not pending).
2. Not a place — online / hybrid / TBA / various.
3. Just a city — "San Francisco, CA" → region only.
4. A room of a known venue — "SFJAZZ Center — Miner Auditorium".
5. Geocode with Nominatim and apply the evidence rules.
6. Otherwise pending, with the reason.

Unknown beats wrong: nothing is assigned unless independent signals agree.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .geocode import BudgetExhausted, Place
from .normalize import names_match, normalize_key, venue_part
from .regions import REGION_OF_COUNTY, city_in_text, city_segment, county_at, whole_string_city
from .store import Store

_ONLINE = re.compile(
    r"\b(online|virtual(ly)?|zoom|livestream(ed)?|live ?stream|webinar|streaming|youtube"
    r"|google meet|crowdcast)\b", re.I)
_HYBRID = re.compile(r"\bhybrid\b|in[- ]person (and|&|\+|or) online|online (and|&|\+|or) in[- ]person", re.I)
_NOT_A_PLACE = re.compile(
    r"\b(tba|tbd|to be (announced|determined)|various( locations)?|multiple locations"
    r"|several locations|see (the )?(event|description|website|page|details)|off-?site"
    r"|secret location|(location|address) (sent|shared|provided|tbd|to come)"
    r"|rsvp (for|to get) (the )?(address|location)|private (residence|home|location))\b"
    r"|^(the )?(san francisco )?bay area$", re.I)

# Sources whose own name for a venue doesn't geocode: query OpenStreetMap by
# the name it knows, and match the result against that name.
_QUERY_HINTS = [
    (re.compile(r"^sfpl — main$"), "San Francisco Public Library Main Library", "San Francisco Public Library Main Library"),
    (re.compile(r"^sfpl — (.+)$"), "{0} Branch Library, San Francisco", "{0} Branch Library"),
]
# For a source whose home is SF, also try "<venue>, San Francisco".
_HOME_CITY = {"sf": "San Francisco"}
# The street-address part of "Name, 57 Wentworth Place, San Francisco, CA" or
# "Book Passage Corte Madera 51 Tamal Vista Blvd …": from the first house
# number followed by a street word.
_ADDRESS = re.compile(r"(?<![\w-])(\d{1,5}[a-z]?\s+(?:[nsew]\.?\s+)?[a-z0-9][\w.'-]*\s.*)$", re.I)
PENDING_RETRY = timedelta(days=7)


@dataclass
class Outcome:
    location: str
    action: str  # known | online | none | outside | region | room | alias | new | pending | deferred
    entry: dict | None
    reason: str | None = None
    evidence: list[str] = field(default_factory=list)


class Resolver:
    def __init__(self, store: Store, geocoder, today: date | None = None,
                 retry_pending: bool = False):
        self.store = store
        self.geo = geocoder
        self.today = today or date.today()
        self.retry_pending = retry_pending

    # --- entry point -----------------------------------------------------

    def resolve(self, location: str, sources=(), events: int = 0) -> Outcome:
        """Resolve one string, record the result in the store, return it."""
        key = normalize_key(location)
        existing = self.store.locations.get(key)
        if existing and "pending" not in existing:
            return Outcome(location, "known", existing)
        if existing and not self._due(existing):
            return Outcome(location, "known", existing)
        try:
            out = self._resolve(location, list(sources))
        except BudgetExhausted:
            # Over this run's lookup cap: leave it for the next run.
            return Outcome(location, "deferred", existing)
        if out.action == "pending":
            prev = (existing or {}).get("pending", {})
            out.entry = {"pending": {
                "reason": out.reason,
                "first_seen": prev.get("first_seen", self.today.isoformat()),
                "last_tried": self.today.isoformat(),
                "events": events,
                "sources": sorted(set(sources)),
            }}
        self.store.locations[key] = out.entry
        return out

    def _due(self, entry: dict) -> bool:
        last = entry["pending"].get("last_tried")
        return self.retry_pending or not last or date.fromisoformat(last) + PENDING_RETRY <= self.today

    # --- the steps -------------------------------------------------------

    def _resolve(self, location: str, sources: list[str]) -> Outcome:
        city = city_in_text(location)
        if _HYBRID.search(location) or (_ONLINE.search(location) and city):
            return Outcome(location, "none", {"place": "none"}, evidence=["online and in person"])
        if _ONLINE.search(location):
            return Outcome(location, "online", {"place": "online"})
        if _NOT_A_PLACE.search(location):
            return Outcome(location, "none", {"place": "none"}, evidence=["not a single place"])
        whole = whole_string_city(location)
        if whole:
            region = REGION_OF_COUNTY[whole[1]]
            return Outcome(location, "region", {"place": "region", "region": region},
                           evidence=[f"text: just a city ({whole[0]})"])

        vp, room = venue_part(location)
        # "Sydney Goldstein Theater 275 Hayes St San Francisco" → the name part.
        m = _ADDRESS.search(vp)
        if m and m.start() > 0:
            vp = vp[:m.start()].strip(" -—,")
        known = self._known_venue(vp)
        if known and normalize_key(vp) != normalize_key(location):
            entry = {"venue": known}
            if room:
                entry["room"] = room
            return Outcome(location, "room", entry, evidence=[f"known venue: {known}"])

        home = next((self.store.source_homes[s] for s in sources if s in self.store.source_homes), None)
        seg = city_segment(location)
        outside_city = seg if seg and not city else None
        reasons = []
        coarse = []  # street/city-level matches: a fallback, never a first answer
        for query, expect in self._queries(location, vp, home):
            for place in self.geo.search(query):
                out = self._judge(location, vp, room, expect, place, city, home, reasons, coarse)
                if out:
                    return out
        if coarse:
            # No building-level match from any query ("Guildhouse, 420 First
            # St" first hit an office park; the name query found the building).
            place, region, evidence = coarse[0]
            if _is_specific(vp):
                # A named place or a street address whose building OpenStreetMap
                # doesn't have: still a venue, just without a precise pin.
                return self._rough_venue(location, vp, room, place, region, evidence)
            # An intersection or a neighbourhood: the region is all we know.
            return Outcome(location, "region", {"place": "region", "region": region},
                           evidence=evidence)
        if outside_city and any(r.startswith("outside:") for r in reasons):
            # The text names a city outside the Bay Area and the map agrees.
            return Outcome(location, "outside", {"place": "outside"},
                           evidence=[f"text: {outside_city.title()}", "map: outside the Bay Area"])
        reasons = [r for r in reasons if not r.startswith("outside:")] or \
            [r.split(":", 1)[1] for r in reasons] or ["no map result in the Bay Area"]
        return Outcome(location, "pending", None, reason=reasons[0])

    def _known_venue(self, vp: str) -> str | None:
        k = normalize_key(vp)
        e = self.store.locations.get(k)
        if e and "venue" in e:
            return e["venue"]
        return next((vid for vid, v in self.store.venues.items()
                     if normalize_key(v.get("name")) == k), None)

    def _queries(self, location: str, vp: str, home: str | None) -> list[tuple[str, str]]:
        key = normalize_key(location)
        out = []
        for pattern, query, expect in _QUERY_HINTS:
            m = pattern.match(key)
            if m:
                groups = [g.title() for g in m.groups()]
                out.append((query.format(*groups), expect.format(*groups)))
        out.append((location, vp))
        m = _ADDRESS.search(location)
        if m and normalize_key(m.group(1)) != key:
            # Just the address; the result must then match the house number.
            out.append((m.group(1), vp))
        if normalize_key(vp) != key:
            out.append((vp, vp))
        if home in _HOME_CITY and not city_in_text(location):
            out.append((f"{vp}, {_HOME_CITY[home]}", vp))
        seen, uniq = set(), []
        for q, e in out:
            if q not in seen:
                seen.add(q)
                uniq.append((q, e))
        return uniq

    def _judge(self, location, vp, room, expect, place: Place, city, home, reasons, coarse) -> Outcome | None:
        """Apply the evidence rules to one map result; None (with a reason
        appended) when it isn't good enough."""
        county = county_at(place.lat, place.lng)
        label = place.name or place.display_name.split(",")[0]
        if county is None:
            reasons.append(f"outside:map result {label!r} is outside the Bay Area")
            return None
        region = REGION_OF_COUNTY[county]
        evidence = [f"map: {label} in {county} County ({place.precision}, {place.osm})"]
        if city:
            if city[1] != county:
                reasons.append(f"text says {city[0].title()} ({city[1]} County), map result "
                               f"{label!r} is in {county} County")
                return None
            evidence.append(f"text: {city[0].title()}")
        if home and not city:
            # An explicit city in the text outranks the source's usual area
            # (off-site events); without one, the home vetoes a far match.
            if home != region:
                reasons.append(f"source's events are usually in {home}, map result {label!r} is in {region}")
                return None
            evidence.append(f"source home: {home}")

        if place.precision != "building":
            if not city:
                reasons.append(f"only a {place.precision}-level map match ({label!r}) and no city in the text")
            else:
                coarse.append((place, region, evidence))
            return None

        name_ok = place.is_poi and names_match(expect, place.name)
        # OpenStreetMap joins a building's numbers: "4704;4706".
        numbers = [n.strip() for n in (place.address.get("house_number") or "").split(";") if n.strip()]
        number_ok = any(re.search(rf"(?<![\d-]){re.escape(n)}(?![\d-])", location) for n in numbers)
        if not (name_ok or number_ok):
            reasons.append(f"map result {label!r} doesn't match the name or street number")
            return None
        if name_ok:
            evidence.append("name matches")
        if number_ok:
            evidence.append("street number matches")
        # Two independent signals must agree on where it is: the map result
        # plus a city in the text, a matching place name, or the source's home.
        if not (city or name_ok or home):
            reasons.append(f"only the map result places {label!r}; nothing else agrees")
            return None
        return self._place_venue(location, vp, room, expect, place, region, evidence, name_ok)

    def _place_venue(self, location, vp, room, expect, place, region, evidence, name_ok) -> Outcome:
        entry_extra = {"room": room} if room else {}
        same = self.store.find_by_osm(place.osm)
        if not same:
            name = place.name if name_ok else expect
            same = next((vid for _, vid in self.store.near(place.lat, place.lng, 100)
                         if names_match(name, self.store.venues[vid]["name"])), None)
        if same:
            return Outcome(location, "alias", {"venue": same, **entry_extra}, evidence=evidence)

        if name_ok:
            name = place.name
        elif re.match(r"^\d", vp) or not re.search(r"[A-Za-z]", vp):
            # An address ("1070 Bryant St") or a name in another script: use
            # OpenStreetMap's name when it has one.
            name = place.name or vp
        else:
            name = vp
        nearby = [vid for _, vid in self.store.near(place.lat, place.lng, 250)]
        fields = {
            "name": name,
            "address": _best_address(location, place),
            "lat": round(place.lat, 6), "lng": round(place.lng, 6),
            "precision": "building",
            "region": region,
            "osm": place.osm,
            "status": "auto",
            "evidence": evidence,
        }
        if nearby:
            fields["possible_duplicates"] = nearby
        vid = self.store.add_venue(fields)
        return Outcome(location, "new", {"venue": vid, **entry_extra}, evidence=evidence)

    def _rough_venue(self, location, vp, room, place, region, evidence) -> Outcome:
        """A venue from a street- or city-level match: coordinates only when
        they're at least on the right street, never a city centroid."""
        fields = {
            "name": vp,
            "address": _text_address(location) or "",
            "precision": place.precision,
            "region": region,
            "status": "auto",
            "evidence": evidence,
        }
        if place.precision == "street":
            fields.update(lat=round(place.lat, 6), lng=round(place.lng, 6))
        vid = self.store.add_venue(fields)
        entry = {"venue": vid, **({"room": room} if room else {})}
        return Outcome(location, "new", entry, evidence=evidence)


def _text_address(location: str) -> str | None:
    """The street address as the source wrote it ("770 West Grand Ave.,
    Suite A, Oakland"), or None."""
    m = _ADDRESS.search(location)
    return re.sub(r"(,\s*(USA|United States))+$", "", m.group(1).strip(), flags=re.I) if m else None


def _best_address(location: str, place: Place) -> str:
    """OpenStreetMap's address when it has the house number; otherwise the
    source's own street address (OSM often pins a venue on a street without
    a number: "West Grand Avenue" for 770 West Grand Ave). A building with
    several numbers ("2300;2310;2314") also defers to the source, which
    names the one it uses."""
    number = place.address.get("house_number") or ""
    if number and ";" not in number:
        return place.street_address()
    return _text_address(location) or place.street_address()


def _is_specific(vp: str) -> bool:
    """A name or a street address — not an intersection ("Montana St &
    Fruitvale Ave") or a bare street."""
    if re.search(r"\s(&|and|at)\s", vp, re.I) and re.search(r"\b(st|ave|blvd|rd|street|avenue)\b", vp, re.I):
        return False
    if re.match(r"^\d", vp):
        return True  # "110 Yacht Rd"
    return bool(re.search(r"[A-Za-z]", vp)) and not re.fullmatch(
        r"[\w\s.'-]*\b(st|ave|blvd|rd|way|street|avenue|boulevard|road|drive|dr)\.?", vp, re.I)
