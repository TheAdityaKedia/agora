from datetime import date, datetime, timezone
from pathlib import Path

from scrapers import calperformances as cp, roxie, uctheatre

FIXTURES = Path(__file__).parent / "fixtures"


def test_calperformances_event_page():
    events = cp.parse_event_page((FIXTURES / "calperformances_event.html").read_text(), "https://cp/x")
    assert len(events) == 1  # repeated widget blocks collapse by start
    ev = events[0]
    assert ev.start_time.tzinfo is not None and ev.url == "https://cp/x"
    assert ev.description and ev.image_url.startswith("https://calperformances.org/")


def test_calperformances_widget_blocks_and_title_suffix():
    block = ('<div class="addeventatc"><span class="start">10/11/2026 03:00 pm</span>'
             '<span class="timezone">America/Los_Angeles</span>'
             '<span class="title">Takács Quartet with Jeremy Denk, piano 2627</span>'
             '<span class="location">Zellerbach Hall</span></div>')
    events = cp.parse_event_page(block + block, "u")
    assert len(events) == 1
    assert events[0].title == "Takács Quartet with Jeremy Denk, piano"
    assert events[0].start_time == datetime(2026, 10, 11, 22, 0, tzinfo=timezone.utc)
    assert events[0].location == f"Zellerbach Hall, {cp.CAMPUS}"


def test_calperformances_locations_and_seasons():
    assert cp.location_for("Hertz Hall") == f"Hertz Hall, {cp.CAMPUS}"
    assert cp.location_for("Henry J. Kaiser Center for the Arts, Oakland") == \
        "Henry J. Kaiser Center for the Arts, Oakland"
    assert cp.location_for("First Church") == \
        "First Congregational Church of Berkeley, 2345 Channing Way, Berkeley, CA 94704"
    assert cp.location_for("Freight") == "Freight, Berkeley, CA"
    assert cp.location_for("") == f"Zellerbach Hall, {cp.CAMPUS}"
    assert cp.seasons(date(2026, 10, 7)) == ("2026-27", "2027-28")
    assert cp.seasons(date(2027, 3, 1)) == ("2026-27", "2027-28")


def test_uctheatre_cards_and_show_time():
    cards = uctheatre.parse_cards((FIXTURES / "uctheatre_home.html").read_text())
    assert [c["title"] for c in cards] == ["PRESIDENT: North American Campaign 2026"]  # "MOVED TO" card dropped
    assert cards[0]["description"] == "with Showing Teeth · Rock"
    assert cards[0]["url"].startswith("https://www.theuctheatre.org/shows/")
    assert cards[0]["image_url"].startswith("https://cdn.prod.website-files.com/")
    # "October 6, 2026 Doors: 7:00 pm • Start: 8:00 pm" → the start time.
    assert uctheatre.parse_start((FIXTURES / "uctheatre_show.html").read_text()) == \
        datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc)


def test_roxie_calendar_one_event_per_screening():
    events = roxie.parse_calendar((FIXTURES / "roxie_calendar.html").read_text())
    assert len(events) == 24
    first = events[0]
    assert first.title == "Ghost in the Shell: 30th Anniversary Remaster"
    assert first.start_time == datetime(2026, 10, 1, 23, 20, tzinfo=timezone.utc)  # Oct 1, 4:20pm PDT
    assert first.url.startswith("https://roxie.com/film/")
    assert [e.start_time for e in events] == sorted(e.start_time for e in events)


def test_roxie_film_page_blurb_and_still():
    desc, image = roxie.parse_film_page((FIXTURES / "roxie_film.html").read_text())
    assert desc.startswith("Director Rafael Manuel")
    assert image.startswith("https://roxie.com/wp-content/uploads/")
