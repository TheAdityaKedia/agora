"""Venue resolution (service/places). No network: geocoder responses are
hand-built in the shape Nominatim returns (format=jsonv2, addressdetails=1)."""
import json
from datetime import date, timedelta

import pytest

from places.geocode import BudgetExhausted, Place
from places.normalize import names_match, normalize_key, venue_part
from places.regions import city_in_text, county_at, region_at, whole_string_city
from places.resolve import Resolver
from places.store import Store

# Real coordinates in each county (and outside the Bay Area).
SPECS = (37.79790, -122.40652)          # Specs' Bar, North Beach, SF
SFJAZZ = (37.77634, -122.42171)
MIGHTY = (37.81260, -122.26680)         # Oakland, Alameda County
WALNUT_CREEK = (37.91010, -122.06520)   # Contra Costa County
SAN_RAFAEL = (37.97350, -122.53110)     # Marin County
LOS_ANGELES = (34.05220, -118.24370)
BERNAL = (37.74110, -122.41720)


def nominatim(name, lat, lng, *, rank=30, category="amenity", type_="theatre",
              osm="node/1", house=None, road=None, city="San Francisco"):
    osm_type, osm_id = osm.split("/")
    address = {k: v for k, v in {"house_number": house, "road": road, "city": city,
                                 "state": "California", "postcode": "94133"}.items() if v}
    return {"lat": str(lat), "lon": str(lng), "name": name, "osm_type": osm_type,
            "osm_id": int(osm_id), "category": category, "type": type_, "place_rank": rank,
            "address": address, "display_name": f"{name}, {city}"}


class FakeGeocoder:
    def __init__(self, responses=None, budget=None):
        self.responses = responses or {}
        self.queries = []
        self.budget = budget

    def search(self, query):
        if self.budget is not None and len(self.queries) >= self.budget:
            raise BudgetExhausted(query)
        self.queries.append(query)
        return [Place.from_json(r) for r in self.responses.get(query, [])]


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path)
    s.source_homes = {"SFJAZZ Center": "sf", "Commonwealth Club": "sf"}
    return s


def resolver(store, responses=None, **kw):
    return Resolver(store, FakeGeocoder(responses, **kw), today=date(2026, 10, 3))


# --- normalisation, regions, names --------------------------------------------

def test_normalize_key():
    assert normalize_key("Specs’, 12 Saroyan Place, San Francisco, CA 94133, USA") == \
        "specs', 12 saroyan place, san francisco, ca"
    assert normalize_key("  SFPL   —  Main ") == "sfpl — main"
    assert normalize_key(None) == ""


def test_city_in_text_reads_only_the_address_city():
    assert city_in_text("Berkeley Rep, 2025 Addison St, Berkeley, CA 94704") == ("berkeley", "Alameda")
    assert city_in_text("123 Vallejo St, San Francisco") == ("san francisco", "San Francisco")
    assert city_in_text("Oakland Art Murmur") is None
    assert city_in_text("Clio's Books, 353 Grand Ave, Oakland CA 94610") == ("oakland", "Alameda")


def test_whole_string_city():
    assert whole_string_city("San Francisco, CA") == ("san francisco", "San Francisco")
    assert whole_string_city("Berkeley") == ("berkeley", "Alameda")
    assert whole_string_city("The Dawn Club, San Francisco") is None


def test_county_at():
    assert county_at(*SPECS) == "San Francisco"
    assert county_at(*MIGHTY) == "Alameda"
    assert county_at(*WALNUT_CREEK) == "Contra Costa"
    assert county_at(*SAN_RAFAEL) == "Marin"
    assert county_at(37.4275, -122.1697) == "Santa Clara"   # Stanford
    assert county_at(37.4530, -122.1817) == "San Mateo"     # Menlo Park
    assert county_at(37.8270, -122.4230) == "San Francisco" # Alcatraz (water belongs to a county)
    assert county_at(*LOS_ANGELES) is None
    assert county_at(36.9741, -122.0308) is None            # Santa Cruz
    assert region_at(*WALNUT_CREEK) == "eastbay"


@pytest.mark.parametrize("a,b,same", [
    ("Specs'", "Specs Bar", True),
    ("Orpheum Theatre", "Orpheum Theater", True),
    ("San Francisco Public Library Main Library", "San Francisco Public Library - Main Library", True),
    ("The Chapel", "Chapel of Grace", False),
    ("Main Library", "Oakland Main Library", False),
    ("SFPL", "San Francisco Public Library", False),
])
def test_names_match(a, b, same):
    assert names_match(a, b) is same


def test_venue_part_splits_rooms():
    assert venue_part("SFJAZZ Center — Miner Auditorium") == ("SFJAZZ Center", "Miner Auditorium")
    assert venue_part("The Berkeley Alembic (Luna), 2820 Seventh St") == ("The Berkeley Alembic", "Luna")
    assert venue_part("Specs', 12 Saroyan Place") == ("Specs'", None)


# --- resolution: places that aren't venues ---------------------------------------

def test_online_hybrid_and_tba(store):
    r = resolver(store)
    assert r.resolve("Online via Zoom").entry == {"place": "online"}
    assert r.resolve("Virtual Event").entry == {"place": "online"}
    assert r.resolve("Hybrid: Main Library + Zoom").entry == {"place": "none"}
    assert r.resolve("Zoom and Book Passage, Corte Madera, CA").entry == {"place": "none"}
    assert r.resolve("Location TBA").entry == {"place": "none"}
    assert r.resolve("Off-site (see event page) · presented by Omnivore Books").entry == {"place": "none"}
    assert r.geo.queries == []


def test_just_a_city_is_region_only(store):
    out = resolver(store).resolve("San Francisco, CA")
    assert out.entry == {"place": "region", "region": "sf"}
    assert store.region_of("San Francisco, CA, USA") == "sf"


# --- resolution: venues ------------------------------------------------------------

def test_poi_with_city_becomes_a_new_venue(store):
    q = "Specs', 12 Saroyan Place, San Francisco, CA"
    out = resolver(store, {q: [nominatim("Specs Bar", *SPECS, house="12", road="Saroyan Place")]}).resolve(
        q, ["SF Bar Guide"], 4)
    assert out.action == "new"
    venue = store.venues[out.entry["venue"]]
    assert venue["name"] == "Specs Bar" and venue["region"] == "sf" and venue["status"] == "auto"
    assert venue["address"].startswith("12 Saroyan Place, San Francisco")
    assert "text: San Francisco" in venue["evidence"] and "name matches" in venue["evidence"]
    assert store.validate() == []


def test_map_result_outside_bay_area_is_pending(store):
    q = "Orpheum Theatre"
    out = resolver(store, {q: [nominatim("Orpheum Theatre", *LOS_ANGELES, city="Los Angeles")]}).resolve(q)
    assert out.action == "pending"
    assert "outside the Bay Area" in store.locations["orpheum theatre"]["pending"]["reason"]


def test_text_city_disagreeing_with_map_is_pending(store):
    q = "Foo Hall, 100 Main St, Oakland, CA"
    out = resolver(store, {q: [nominatim("Foo Hall", *WALNUT_CREEK, city="Walnut Creek")]}).resolve(q)
    assert out.action == "pending"
    assert "text says Oakland" in out.reason


def test_different_name_is_not_accepted(store):
    q = "The Chapel, San Francisco"
    out = resolver(store, {q: [nominatim("Chapel of Grace", *SPECS)]}).resolve(q)
    assert out.action == "pending" and "doesn't match" in out.reason


def test_source_home_vetoes_a_far_away_match(store):
    q = "Dominican University"
    resp = {q: [nominatim("Dominican University", *SAN_RAFAEL, category="amenity", type_="university")]}
    out = resolver(store, resp).resolve(q, ["Commonwealth Club"])
    assert out.action == "pending" and "usually in sf" in out.reason


def test_street_number_without_any_agreeing_signal_is_pending(store):
    q = "480 23rd Street"
    resp = {q: [nominatim("", *MIGHTY, rank=30, category="place", type_="house",
                          house="480", road="23rd Street", city="Oakland")]}
    out = resolver(store, resp).resolve(q, ["Partiful"])
    assert out.action == "pending" and "nothing else agrees" in out.reason


def test_street_number_plus_source_home_is_accepted(store):
    store.source_homes["The Commons (SF)"] = "sf"
    q = "550 Laguna St, San Francisco + North Studio"
    resp = {q: [nominatim("", *SFJAZZ, rank=30, category="place", type_="house",
                          house="550", road="Laguna Street")]}
    out = resolver(store, resp).resolve(q, ["The Commons (SF)"])
    assert out.action == "new"
    assert "street number matches" in out.evidence and "source home: sf" in out.evidence


def test_city_level_match_needs_a_city_in_the_text(store):
    q = "Somewhere vague"
    city_result = nominatim("Oakland", *MIGHTY, rank=16, category="place", type_="city", city="Oakland")
    assert resolver(store, {q: [city_result]}).resolve(q).action == "pending"


def test_intersection_with_street_level_match_is_region_only(store):
    q = "Montana St & Fruitvale Ave, Oakland, CA"
    street = nominatim("Montana Street", *MIGHTY, rank=26, category="highway", type_="residential", city="Oakland")
    assert resolver(store, {q: [street]}).resolve(q).entry == {"place": "region", "region": "eastbay"}


def test_named_place_with_street_level_match_is_a_rough_venue(store):
    q = "Washington Square Park, Filbert & Stockton St, San Francisco, CA 94133"
    street = nominatim("Filbert Street", *SPECS, rank=26, category="highway", type_="residential")
    out = resolver(store, {q: [street]}).resolve(q, ["Partiful"])
    v = store.venues[out.entry["venue"]]
    assert v["name"] == "Washington Square Park" and v["precision"] == "street" and v["region"] == "sf"
    assert "lat" in v
    # A city-level match: a venue, but no coordinates (a city centroid is no pin).
    q2 = "The Green Room, San Francisco, CA"
    city = nominatim("San Francisco", *SFJAZZ, rank=16, category="place", type_="city")
    out2 = resolver(store, {q2: [city]}).resolve(q2, ["Herbst / Davies (SF War Memorial)"])
    v2 = store.venues[out2.entry["venue"]]
    assert v2["name"] == "The Green Room" and v2["precision"] == "city" and "lat" not in v2
    assert store.validate() == []


def test_address_only_with_street_level_match_is_a_venue(store):
    q = "110 Yacht Rd, San Francisco, CA 94123"
    street = nominatim("Yacht Road", 37.8070, -122.4450, rank=26, category="highway", type_="service")
    out = resolver(store, {q: [street]}).resolve(q, ["Partiful"])
    assert out.action == "new" and store.venues[out.entry["venue"]]["name"] == "110 Yacht Rd"


def test_address_comes_from_the_text_when_osm_has_no_house_number(store):
    q = "Transmission Gallery, 770 West Grand Ave., Suite A, Oakland"
    poi = nominatim("Transmission Gallery", *MIGHTY, category="tourism", type_="gallery",
                    road="West Grand Avenue", city="Oakland")
    out = resolver(store, {q: [poi]}).resolve(q, ["Oakland Art Murmur"])
    assert store.venues[out.entry["venue"]]["address"] == "770 West Grand Ave., Suite A, Oakland"


def test_room_of_known_venue_needs_no_lookup(store):
    q = "SFJAZZ Center — Miner Auditorium"
    resp = {"SFJAZZ Center": [nominatim("SFJAZZ Center", *SFJAZZ, osm="way/77")]}
    r = resolver(store, resp)
    first = r.resolve(q, ["SFJAZZ Center"])
    assert first.action == "new" and first.entry["room"] == "Miner Auditorium"
    calls = len(r.geo.queries)
    second = r.resolve("SFJAZZ Center — Joe Henderson Lab", ["SFJAZZ Center"])
    assert second.action == "room"
    assert second.entry == {"venue": first.entry["venue"], "room": "Joe Henderson Lab"}
    assert len(r.geo.queries) == calls


def test_same_osm_object_or_nearby_same_name_is_an_alias(store):
    resp = {
        "Specs', 12 Saroyan Place, San Francisco": [nominatim("Specs Bar", *SPECS, osm="node/9")],
        "Specs Bar, San Francisco": [nominatim("Specs Bar", *SPECS, osm="node/9")],
        "Specs Twelve Adler Museum Cafe, San Francisco": [
            nominatim("Specs Twelve Adler Museum Cafe", SPECS[0] + 0.0003, SPECS[1], osm="node/10")],
    }
    r = resolver(store, resp)
    vid = r.resolve("Specs', 12 Saroyan Place, San Francisco").entry["venue"]
    assert r.resolve("Specs Bar, San Francisco").entry == {"venue": vid}
    # ~33 m away and a different name: a separate venue, flagged as a possible duplicate.
    other = r.resolve("Specs Twelve Adler Museum Cafe, San Francisco")
    assert other.action == "new"
    assert store.venues[other.entry["venue"]]["possible_duplicates"] == [vid]


def test_sfpl_branch_hint(store):
    store.source_homes["San Francisco Public Library"] = "sf"
    q = "Bernal Heights Branch Library, San Francisco"
    resp = {q: [nominatim("Bernal Heights Branch Library", *BERNAL, category="amenity", type_="library")]}
    r = resolver(store, resp)
    out = r.resolve("SFPL — Bernal Heights", ["San Francisco Public Library"])
    assert out.action == "new" and r.geo.queries[0] == q


def test_budget_exhausted_defers_without_recording(store):
    out = resolver(store, budget=0).resolve("Some New Bar, San Francisco")
    assert out.action == "deferred"
    assert "some new bar, san francisco" not in store.locations


def test_pending_is_retried_weekly(store):
    r = resolver(store)
    r.resolve("Mystery Spot")
    assert store.locations["mystery spot"]["pending"]["last_tried"] == "2026-10-03"
    n = len(r.geo.queries)
    r.resolve("Mystery Spot")
    assert len(r.geo.queries) == n  # tried today: not again
    r.today = date(2026, 10, 3) + timedelta(days=7)
    r.resolve("Mystery Spot")
    assert len(r.geo.queries) > n
    assert store.locations["mystery spot"]["pending"]["first_seen"] == "2026-10-03"


# --- the committed files ----------------------------------------------------------

def test_validate_catches_bad_edits(store):
    store.venues["specs"] = {"name": "Specs", "region": "eastbay", "status": "auto",
                             "precision": "building", "lat": SPECS[0], "lng": SPECS[1]}
    store.venues["Bad Id"] = {"name": "x", "region": "mars", "status": "maybe", "precision": "exact"}
    store.locations["Not Normalised"] = {"venue": "specs"}
    store.locations["ghost"] = {"venue": "nope"}
    store.locations["both"] = {"venue": "specs", "place": "none"}
    store.locations["pend"] = {"pending": {}}
    errs = "\n".join(store.validate())
    for needle in ["coordinates are in sf, but region says eastbay", "lowercase slug",
                   "unknown region 'mars'", "status must be", "precision must be",
                   "key is not normalised", "unknown venue 'nope'", "exactly one of",
                   "pending needs a reason"]:
        assert needle in errs, needle


def test_save_round_trips_byte_identically(store, tmp_path):
    store.venues["specs-bar"] = {"name": "Specs Bar", "region": "sf", "status": "verified",
                                 "precision": "building", "lat": SPECS[0], "lng": SPECS[1]}
    store.locations["specs bar"] = {"venue": "specs-bar"}
    store.save()
    first = (tmp_path / "venues.json").read_text(), (tmp_path / "venue_locations.json").read_text()
    Store(tmp_path).save()
    assert ((tmp_path / "venues.json").read_text(), (tmp_path / "venue_locations.json").read_text()) == first
    assert json.loads(first[1])["locations"] == {"specs bar": {"venue": "specs-bar"}}


def test_committed_venue_files_are_valid():
    errs = Store().validate()
    assert errs == [], "\n".join(errs)


def test_name_plus_address_falls_back_to_the_address(store):
    q = "Lion's Den Lounge and Bar, 57 Wentworth Place, San Francisco, CA"
    resp = {"57 Wentworth Place, San Francisco, CA": [
        nominatim("", 37.7949, -122.4067, rank=30, category="place", type_="house",
                  house="57", road="Wentworth Place")]}
    r = resolver(store, resp)
    out = r.resolve(q, ["SF Bar Guide"])
    assert r.geo.queries[:2] == [q, "57 Wentworth Place, San Francisco, CA"]
    assert out.action == "new"
    venue = store.venues[out.entry["venue"]]
    assert venue["name"] == "Lion's Den Lounge and Bar"
    assert "street number matches" in out.evidence and "text: San Francisco" in out.evidence


def test_city_run_on_after_street_number():
    assert city_in_text("Book Passage Corte Madera 51 Tamal Vista Blvd Corte Madera, CA 94925") == \
        ("corte madera", "Marin")
    assert city_in_text("Calvary Presbyterian 2515 Fillmore St San Francisco, CA") == \
        ("san francisco", "San Francisco")


def test_name_ending_with_the_venue_name():
    assert names_match("Herbst Theatre", "War Memorial Veterans Building Herbst Theatre")
    assert names_match("Opera House", "War Memorial Opera House")
    assert not names_match("The Chapel", "Grace Chapel")


def test_joined_house_numbers_match(store):
    q = "Bottom's Up, 4704 Mission St., San Francisco, CA"
    resp = {q: [nominatim("", 37.7210, -122.4370, category="building", type_="yes",
                          house="4704;4706", road="Mission Street")]}
    assert resolver(store, resp).resolve(q, ["SF Bar Guide"]).action == "new"


def test_joined_house_numbers_take_the_text_address(store):
    q = "2300 Chestnut St, San Francisco, CA 94123"
    resp = {q: [nominatim("", 37.8003, -122.4405, category="building", type_="yes",
                          house="2300;2310;2314;2320", road="Chestnut Street",
                          city="San Francisco")]}
    out = resolver(store, resp).resolve(q, ["Partiful"])
    assert store.venues[out.entry["venue"]]["address"] == "2300 Chestnut St, San Francisco, CA 94123"


def test_street_address_keeps_the_first_of_joined_numbers():
    place = Place.from_json(nominatim("", 37.8003, -122.4405, house="2300;2310", road="Chestnut Street",
                                      city="San Francisco"))
    assert place.street_address().startswith("2300 Chestnut Street, San Francisco")


def test_named_city_outside_bay_area_is_outside_not_pending(store):
    q = "1001 Center St, Santa Cruz, CA"
    resp = {q: [nominatim("Food Lounge", 36.9741, -122.0308, city="Santa Cruz")]}
    out = resolver(store, resp).resolve(q, ["Sunset Trivia"])
    assert out.entry == {"place": "outside"}
    assert store.region_of(q) is None


def test_text_city_outranks_source_home(store):
    q = "Dominican University, San Rafael"
    resp = {q: [nominatim("Dominican University of California", *SAN_RAFAEL, type_="university")]}
    out = resolver(store, resp).resolve(q, ["Commonwealth Club"])
    assert out.action == "new" and store.venues[out.entry["venue"]]["region"] == "northbay"


def test_venue_name_drops_a_run_on_address(store):
    q = "Sydney Goldstein Theater 275 Hayes St San Francisco, CA"
    resp = {q: [nominatim("Nourse Theater", 37.7772, -122.4214, house="275", road="Hayes Street")]}
    out = resolver(store, resp).resolve(q, ["City Arts & Lectures"])
    assert out.action == "new"
    assert store.venues[out.entry["venue"]]["name"] == "Sydney Goldstein Theater"


def test_bay_area_alone_is_not_a_place(store):
    assert resolver(store).resolve("San Francisco Bay Area").entry == {"place": "none"}


def test_name_in_another_script_uses_the_map_name(store):
    q = "フェリービルディング, 1 Ferry Building, San Francisco, CA"
    resp = {q: [nominatim("San Francisco Ferry Building", 37.795549, -122.393475,
                          category="building", type_="yes", house="1", road="The Embarcadero")]}
    out = resolver(store, resp).resolve(q, ["Partiful"])
    assert out.entry["venue"] == "san-francisco-ferry-building"
    assert store.venues["san-francisco-ferry-building"]["name"] == "San Francisco Ferry Building"


def test_coarse_match_waits_for_a_building_level_one(store):
    q = "Guildhouse, 420 First St, San Jose, CA 95113, USA"
    office = nominatim("North First Street Office Center", 37.38312, -121.92372, rank=20,
                       category="place", type_="neighbourhood", city="San Jose")
    poi = nominatim("Guildhouse", 37.32969, -121.88549, category="amenity", type_="bar",
                    house="420", road="South 1st Street", city="San Jose")
    resp = {q: [office], "420 First St, San Jose, CA 95113": [office], "Guildhouse": [poi]}
    out = resolver(store, resp).resolve(q, ["Big Brain Lectures — Bay Area"])
    assert out.action == "new"
    v = store.venues[out.entry["venue"]]
    assert v["name"] == "Guildhouse" and v["precision"] == "building" and v["region"] == "southbay"


def test_named_park_counts_as_a_precise_place():
    park = Place.from_json(nominatim("Washington Square Park", *SPECS, rank=24,
                                     category="leisure", type_="park"))
    assert park.precision == "building" and park.is_poi
    area = Place.from_json(nominatim("Embarcadero Center 2", *SPECS, rank=24,
                                     category="landuse", type_="commercial"))
    assert area.precision != "building"


# --- the pipeline step ----------------------------------------------------------

def test_resolve_locations_summary(store):
    from places.pipeline import collect_locations, resolve_locations
    q = "Specs', 12 Saroyan Place, San Francisco, CA"
    locs = collect_locations([
        {"location": q, "sources": ["SF Bar Guide"]},
        {"location": q, "sources": ["SF Bar Guide"]},
        {"location": "Online via Zoom", "sources": ["City Lights"]},
        {"location": "Mystery Spot", "sources": ["Partiful"]},
        {"location": "", "sources": ["Partiful"]},
    ])
    geo = FakeGeocoder({q: [nominatim("Specs Bar", *SPECS, house="12", road="Saroyan Place")]})
    summary = resolve_locations(locs, store, geo, today=date(2026, 10, 3))
    assert summary["actions"] == {"new": 1, "online": 1, "pending": 1}
    assert summary["new_venues"] == ["specs-bar"]
    assert summary["new_pending"] == ["mystery spot"] and summary["pending"] == ["mystery spot"]
    assert summary["events_with_location"] == 4 and summary["unresolved_events"] == 1
    # A second run: nothing new, nothing looked up again.
    again = resolve_locations(locs, store, geo, today=date(2026, 10, 3))
    assert again["new_pending"] == [] and again["new_venues"] == []
    assert again["actions"] == {"known": 3}


def test_resolve_locations_refuses_invalid_files(store):
    from places.pipeline import resolve_locations
    store.locations["ghost"] = {"venue": "nope"}
    geo = FakeGeocoder()
    summary = resolve_locations({"x": {"text": "x", "events": 1, "sources": []}}, store, geo)
    assert "invalid" in summary and geo.queries == []


def test_main_resolve_places_reads_upcoming_events_and_saves(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from unittest.mock import patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import main
    from models import Base, Event
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    for i, (when, loc) in enumerate([(now + timedelta(days=2), "Online via Zoom"),
                                     (now - timedelta(days=3), "Long Gone Hall, Oakland")]):
        session.add(Event(title=f"e{i}", start_time=when, location=loc, url=f"https://e/{i}",
                          sources=["Src"], created_at=now))
    session.commit()
    with patch("main.get_session", return_value=session):
        summary = main.resolve_places(geocoder=FakeGeocoder(), data_dir=tmp_path, log=lambda *a: None)
    assert summary["actions"] == {"online": 1}           # the past event isn't looked at
    saved = json.loads((tmp_path / "venue_locations.json").read_text())["locations"]
    assert saved == {"online via zoom": {"place": "online"}}
