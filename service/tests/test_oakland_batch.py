"""Oakland batch: Paramount (Carbonhouse list + detail), Oakland Public
Library (BiblioCommons API), EastSide Arts Alliance, and the tribe wrappers
for OACC and Oakland United Beerworks."""
import json
from datetime import datetime, timezone
from pathlib import Path

from scrapers import eastsidearts, oacc, oaklandlibrary, oaklandunited, paramount, tribe_events

FIXTURES = Path(__file__).parent / "fixtures"


def test_paramount_list_cards():
    events = paramount.parse_list((FIXTURES / "paramount_list.html").read_text())
    assert [e.title for e in events] == ["Luna Mexicana", "Frankenstein", "Luna Mexicana"]
    # Oct. 30 | 2026, 7:30 PM Pacific (PDT) → 02:30 UTC next day.
    assert events[0].start_time == datetime(2026, 10, 31, 2, 30, tzinfo=timezone.utc)
    assert events[1].start_time == datetime(2026, 10, 31, 22, 0, tzinfo=timezone.utc)
    assert events[0].url == "https://www.paramountoakland.org/events/detail/luna-mexicana-1"
    assert events[0].description == "Oakland Ballet Presents"
    assert events[0].location == paramount.ADDRESS and events[0].image_url


def test_paramount_start_parsing():
    assert paramount.parse_start("Nov. 1 | 2026", "Event Starts 2:30 PM") == \
        datetime(2026, 11, 1, 22, 30, tzinfo=timezone.utc)  # PDT ends Nov 1 at 2am → PST
    assert paramount.parse_start("Sept. 5 | 2027", "") == datetime(2027, 9, 6, 3, 0, tzinfo=timezone.utc)
    assert paramount.parse_start("TBA", "Event Starts 8:00 PM") is None


def test_paramount_detail_drops_ticketing_promo():
    desc, image = paramount.parse_detail((FIXTURES / "paramount_detail.html").read_text())
    assert desc.startswith("Luna Mexicana is one of the Bay Area")
    assert "Early Bird" not in desc and "Sponsored" not in desc
    assert image.startswith("https://www.paramountoakland.org/assets/img/")


def test_oaklandlibrary_filters_and_maps_branches():
    data = json.loads((FIXTURES / "bibliocommons_oaklandlibrary.json").read_text())
    events = oaklandlibrary.parse_page(data)
    titles = sorted(e.title for e in events)
    # Kept: adult+teen art lab, teen crafts. Dropped: cancelled, toddler
    # storytime (type), Play Cafe (kids+families only), family matinee
    # (kids/tweens/families + teens → teens keeps it).
    assert titles == ["Family Movie Matinee Pop-Up", "Open Art Lab", "Teen Pop Up Crafts"]
    art = next(e for e in events if e.title == "Open Art Lab")
    assert art.url.startswith("https://oaklandlibrary.bibliocommons.com/events/")
    assert art.image_url and art.description and "accessibility information" not in art.description
    assert art.start_time.tzinfo is not None
    crafts = next(e for e in events if e.title == "Teen Pop Up Crafts")
    assert crafts.image_url is None  # stock per-type artwork is skipped


def test_oaklandlibrary_location_labels():
    addr = {"number": "5366", "street": "College Avenue", "city": "Oakland", "zip": "94618"}
    assert oaklandlibrary.location_for({"name": "Rockridge Branch", "address": addr}) == \
        "Oakland Public Library Rockridge Branch, 5366 College Avenue, Oakland, CA 94618"
    main = {"number": "125", "street": "14th Street", "city": "Oakland", "zip": "94612"}
    assert oaklandlibrary.location_for({"name": "Main Library", "address": main}) == \
        "Oakland Main Library, 125 14th Street, Oakland, CA 94612"
    assert oaklandlibrary.location_for({"name": "Main Library TeenZone", "address": main}) == \
        "Oakland Main Library — TeenZone, 125 14th Street, Oakland, CA 94612"
    assert oaklandlibrary.location_for(None) == oaklandlibrary.FALLBACK_LOCATION
    fruitvale = {"number": "3301", "street": "East 12th Street, Suite 271", "city": "Oakland", "zip": "94601"}
    assert oaklandlibrary.location_for({"name": "Fruitvale Branch", "address": fruitvale}) == \
        "Oakland Public Library Fruitvale Branch, 3301 East 12th Street, Oakland, CA 94601"
    history = {"number": "125", "street": "14th Street, 2nd Floor", "city": "Oakland", "zip": "94612"}
    assert oaklandlibrary.location_for({"name": "Oakland History Center", "address": history}) == \
        "Oakland Main Library — Oakland History Center, 125 14th Street, Oakland, CA 94612"


def test_eastsidearts_drops_bare_here_links():
    assert eastsidearts.clean_description("Workshop w/ Sierra Salome  HERE") == "Workshop w/ Sierra Salome"
    assert eastsidearts.clean_description("HERE Join us") == "Join us"
    assert eastsidearts.clean_description("THERE is HEREford") == "THERE is HEREford"
    assert eastsidearts.clean_description(None) is None


def test_oakland_tribe_wrappers(monkeypatch):
    calls = []
    monkeypatch.setattr(tribe_events, "scrape_events",
                        lambda base, **kw: calls.append((base, kw["fallback_location"])) or [])
    for mod in (oacc, oaklandunited):
        mod.scrape()
    assert calls == [(m.SITE_BASE, m.ADDRESS) for m in (oacc, oaklandunited)]


def test_oakland_matches():
    assert paramount.matches("https://www.paramountoakland.org/events")
    assert oaklandlibrary.matches("https://oaklandlibrary.org/events/")
    assert oacc.matches("https://oacc.cc/")
    assert oaklandunited.matches("https://oaklandunitedbeerworks.com/events/")
    assert eastsidearts.matches("https://www.eastsideartsalliance.org/calendar")
