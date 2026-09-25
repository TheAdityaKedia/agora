import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scrapers import bookmanager, tallyho

FIXTURE = Path(__file__).parent / "fixtures" / "bookmanager_events.json"
UTC = ZoneInfo("UTC")


def _events():
    rows = json.loads(FIXTURE.read_text())["rows"]
    return bookmanager.parse_events(rows, site_base="https://tallyhobookstore.com",
                                    fallback_location="Tally Ho! Books, Oakland")


def test_local_date_and_time_to_utc():
    ev = _events()[0]
    assert ev.title == "Book Event with Mahmoud Khalil"
    # 2026-10-03 12:30 PDT -> 19:30 UTC
    assert ev.start_time == datetime(2026, 10, 3, 19, 30, tzinfo=UTC)


def test_location_text_else_fallback():
    evs = _events()
    assert evs[0].location == "First Congregational Church of Oakland"
    assert evs[1].location == "Tally Ho! Books, Oakland"


def test_url_description_image():
    ev = _events()[1]
    assert ev.url == "https://tallyhobookstore.com/events/6179020261020"
    assert ev.description and "<p>" not in ev.description
    assert ev.image_url.startswith("https://cdn1.bookmanager.com/")


def test_all_day_event_at_local_midnight_and_summary_fallback():
    ev = _events()[2]
    assert ev.start_time == datetime(2026, 11, 1, 7, 0, tzinfo=UTC)
    assert ev.description == "Fair"
    assert ev.image_url is None


def test_tallyho_matches():
    assert tallyho.matches("https://tallyhobookstore.com/events")


def test_strips_leading_ticket_call_to_action():
    desc = _events()[0].description
    assert not desc.lower().startswith("click here")
    assert desc.startswith("Join AROC Action")


def test_keeps_long_paragraphs_that_merely_start_with_click_here():
    rows = [{"id": 1, "title": "T", "date": "20261101", "start_time": "19:00:00",
             "description": "<p>Click here to learn about our long-running series, which has hosted "
                            "hundreds of poets over twenty years in the back room.</p>"}]
    [ev] = bookmanager.parse_events(rows, site_base="https://x.test")
    assert ev.description.startswith("Click here to learn")
