from datetime import datetime, timezone
from pathlib import Path

from scrapers import anotherplanet, castro, foxoakland, greekberkeley

FIXTURES = Path(__file__).parent / "fixtures"


def test_microdata_and_list_cards():
    events = anotherplanet.parse((FIXTURES / "anotherplanet_fox.html").read_text(), venue="FOX")
    by_title = {e.title: e for e in events}
    # Carousel card: microdata content "October 6, 2026 8:00pm" (PDT).
    cavetown = by_title["Cavetown"]
    assert cavetown.start_time == datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc)
    assert cavetown.url == "https://thefoxoakland.com/events/cavetown-261006"
    assert cavetown.description == "Running With Scissors Tour"
    # List card: "7:30 pm" with a space; poster comes from its own link.
    floyd = by_title["Brit Floyd"]
    assert floyd.start_time == datetime(2026, 10, 15, 2, 30, tzinfo=timezone.utc)
    assert floyd.image_url.endswith("_BritFloyd_TVSlide-353x192.jpg")
    assert all(e.location == "FOX" for e in events)


def test_list_card_without_microdata_uses_show_time():
    [ev] = anotherplanet.parse((FIXTURES / "anotherplanet_castro.html").read_text(), venue="CASTRO")
    assert ev.title == "Emergency Intercom Live"
    # "Friday, October 09, 2026 Doors: 7:00 pm | Show: 8:00 pm" → the show time.
    assert ev.start_time == datetime(2026, 10, 10, 3, 0, tzinfo=timezone.utc)


def test_duplicate_carousel_card_is_dropped_and_shared_blocks_skipped():
    card = ('<div><a href="/s1"><h2 class="show-title">Show</h2></a>'
            '<div class="date-show" itemprop="startDate" content="October 6, 2026 8:00pm"></div></div>')
    shared = ('<div><a href="/a"><h2 class="show-title">A</h2></a><a href="/b"><h2 class="show-title">B</h2></a>'
              '<div class="date-show" itemprop="startDate" content="October 7, 2026 8:00pm"></div></div>')
    events = anotherplanet.parse(card + card + shared, venue="V")
    assert [e.title for e in events] == ["Show"]  # one copy; A/B have no date of their own


def test_wrappers():
    assert castro.matches("https://thecastro.com/")
    assert foxoakland.matches("https://thefoxoakland.com/")
    assert greekberkeley.matches("https://thegreekberkeley.com/")
    assert not castro.matches("https://thefoxoakland.com/")
