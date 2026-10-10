from datetime import date, datetime, timezone
from pathlib import Path

from scrapers import augusthall, bimbos, feinsteins, ticketweb

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 7)


def test_event_page_without_year_uses_weekday():
    ev = ticketweb.parse_event((FIXTURES / "ticketweb_event_noyear.html").read_text(),
                               url="https://bimbos365club.com/tm-event/fightmaster/", venue="V", today=TODAY)
    assert ev.title == "FIGHTMASTER"
    assert ev.start_time == datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc)  # Wed Oct 14, 8pm PDT
    assert ev.description == "with Ill Peach"  # the logistics-only blurb is dropped
    assert ev.image_url.startswith("https://i.ticketweb.com/")


def test_event_page_with_year_and_show_time():
    ev = ticketweb.parse_event((FIXTURES / "ticketweb_event_year.html").read_text(),
                               url="u", venue="V", today=TODAY)
    assert ev.title == "The Rasmus"
    assert ev.start_time == datetime(2026, 10, 9, 2, 30, tzinfo=timezone.utc)  # Thu Oct 8, 7:30pm PDT


def test_parse_start_variants():
    assert ticketweb.parse_start("Wednesday, October 7, 2026", "8:00 pm", TODAY) == \
        datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)
    assert ticketweb.parse_start("Fri | Oct 09", "Show: 8:00 pm", TODAY) == \
        datetime(2026, 10, 10, 3, 0, tzinfo=timezone.utc)
    # Weekday pins the year: Fri Jan 8 is 2027.
    assert ticketweb.parse_start("Fri | Jan 08", "Show: 7:00 pm", TODAY).year == 2027
    assert ticketweb.parse_start("TBA", "8:00 pm", TODAY) is None


def test_description_strips_box_office_phrases():
    html = ('<div class="tw-name">X</div><span class="tw-event-date-complete">Wednesday, October 7, 2026</span>'
            '<div class="tw-description">2 Drink Minimum Required Please note - delivery delay. </div>'
            '<div class="tw-attractions">with Y</div>')
    assert ticketweb.parse_event(html, url="u", venue="V", today=TODAY).description == "with Y"
    html = html.replace("2 Drink Minimum Required Please note - delivery delay. ",
                        "A soul revue. 21+ ONLY. VIP Includes: meet and greet")
    assert ticketweb.parse_event(html, url="u", venue="V", today=TODAY).description == "with Y · A soul revue."


def test_listing_links_and_next_page():
    html = (FIXTURES / "ticketweb_listing.html").read_text()
    links = ticketweb.listing_links(html)
    assert len(links) == 3 and all("/tm-event/" in u for u in links)
    assert ticketweb.next_page(html, 1) == "https://bimbos365club.com/shows/page/2/"
    assert ticketweb.next_page(html, 2) is None


def test_wrappers():
    assert bimbos.matches("https://bimbos365club.com/events/")
    assert augusthall.matches("https://www.augusthallsf.com/events/")
    assert feinsteins.matches("https://www.feinsteinssf.com/events/")
