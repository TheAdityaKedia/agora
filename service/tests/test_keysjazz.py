from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import keysjazz

FIX = Path(__file__).parent / "fixtures"
UTC = ZoneInfo("UTC")


def _read(name: str) -> str:
    return (FIX / name).read_text()


# --- matches -----------------------------------------------------------------

def test_matches():
    assert keysjazz.matches("https://keysjazzbistro.com/upcoming-shows/")
    assert keysjazz.matches("https://keysjazzbistro.com/event/dave-tull-jazz-laughter/")
    assert not keysjazz.matches("https://birdbeckett.com/events-calendar/")


# --- parse_listing -----------------------------------------------------------

def test_parse_listing_extracts_unique_event_urls():
    urls = keysjazz.parse_listing(_read("keysjazz_listing.html"))
    assert urls == [
        "https://keysjazzbistro.com/event/dave-tull-jazz-laughter/",
        "https://keysjazzbistro.com/event/marcus-shelby-quartet/",
        "https://keysjazzbistro.com/event/late-set-simon-rowe-organ-trio-66/",
    ]
    # nav links (event-calendar, private-event) are excluded; duplicate slug collapsed
    assert all("/event/" in u for u in urls)
    assert len(urls) == len(set(urls))


# --- parse_event_page: one RawEvent per showtime -----------------------------

@pytest.fixture
def marcus():
    return keysjazz.parse_event_page(
        _read("keysjazz_event.html"),
        "https://keysjazzbistro.com/event/marcus-shelby-quartet/",
    )


def test_one_event_per_showtime(marcus):
    # Two sets (7pm + 9pm) on the same night → two events; breadcrumb LD ignored.
    assert len(marcus) == 2
    assert all(isinstance(e, RawEvent) for e in marcus)
    starts = sorted(e.start_time for e in marcus)
    assert starts[0] == datetime(2026, 10, 4, 2, 0, tzinfo=UTC)   # 19:00 -07:00
    assert starts[1] == datetime(2026, 10, 4, 4, 0, tzinfo=UTC)   # 21:00 -07:00
    assert all(e.start_time.tzinfo is not None for e in marcus)


def test_event_fields(marcus):
    e = marcus[0]
    assert e.title == "Marcus Shelby Quartet Celebrates Monk and Trane"
    assert e.url == "https://keysjazzbistro.com/event/marcus-shelby-quartet/"
    assert e.location and "Keys Jazz Bistro" in e.location
    assert e.image_url and e.image_url.startswith("https://")
    assert e.description and e.description.startswith("Marcus Anthony Shelby")
    # truncation marker stripped
    assert "[...]" not in e.description and "[" not in e.description[-3:]


# --- parse_event_page: entity decoding + description cleanup -----------------

def test_html_entities_and_description_cleanup():
    evs = keysjazz.parse_event_page(
        _read("keysjazz_event_entities.html"),
        "https://keysjazzbistro.com/event/dave-tull-jazz-laughter/",
    )
    assert len(evs) == 1
    e = evs[0]
    assert e.title == "Dave Tull -Jazz & Laughter"      # &#038; decoded
    assert e.start_time == datetime(2026, 9, 25, 2, 0, tzinfo=UTC)
    assert "&#038;" not in e.title and "&amp;" not in e.title
    # entities decoded, no leftover &nbsp; and no trailing truncation marker
    assert "&nbsp;" not in e.description and "&hellip;" not in e.description
    assert not e.description.rstrip().endswith("[…]")


def test_url_strips_query_when_no_offer():
    # An event lacking offers.url falls back to the (query-stripped) page URL.
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"Event","name":"X","startDate":"2026-10-05T20:00:00-07:00"}'
        '</script></head><body></body></html>'
    )
    evs = keysjazz.parse_event_page(
        html, "https://keysjazzbistro.com/event/x/?se-date=99999"
    )
    assert len(evs) == 1
    assert evs[0].url == "https://keysjazzbistro.com/event/x/"


def test_skips_when_no_title_or_start():
    html = (
        '<html><head><script type="application/ld+json">'
        '[{"@type":"Event","name":"","startDate":"2026-10-05T20:00:00-07:00"},'
        '{"@type":"Event","name":"No date"}]'
        '</script></head></html>'
    )
    assert keysjazz.parse_event_page(html, "https://keysjazzbistro.com/event/y/") == []


# --- scrape() flow (offline, via injected fetcher) ---------------------------

def test_scrape_flow_fetches_listing_then_pages():
    pages = {
        keysjazz.LISTING_URL: _read("keysjazz_listing.html"),
        "https://keysjazzbistro.com/event/dave-tull-jazz-laughter/": _read("keysjazz_event_entities.html"),
        "https://keysjazzbistro.com/event/marcus-shelby-quartet/": _read("keysjazz_event.html"),
        # late-set page intentionally absent → fetch returns None → skipped gracefully
    }
    events = keysjazz.scrape_with_fetcher(lambda u: pages.get(u))
    # 2 Marcus showtimes + 1 Dave Tull, missing page skipped
    assert len(events) == 3
    titles = sorted({e.title for e in events})
    assert titles == ["Dave Tull -Jazz & Laughter", "Marcus Shelby Quartet Celebrates Monk and Trane"]
