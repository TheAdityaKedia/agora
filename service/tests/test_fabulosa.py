from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scrapers import fabulosa

FIXTURE = Path(__file__).parent / "fixtures" / "fabulosa_events.html"
UTC = ZoneInfo("UTC")
TODAY = date(2026, 9, 24)


def _events():
    return fabulosa.parse_events(FIXTURE.read_text(), TODAY)


def test_one_event_per_block_ignoring_newsletter_heading():
    evs = _events()
    assert len(evs) == 2


def test_title_includes_author_and_date_is_parsed():
    ev = _events()[0]
    assert ev.title == "Thank You for My Life — Jim Cartwright"
    # Tuesday, September 29th at 7pm PDT -> 02:00 UTC Sep 30
    assert ev.start_time == datetime(2026, 9, 30, 2, 0, tzinfo=UTC)


def test_two_line_heading_title_and_author():
    ev = _events()[1]
    assert ev.title == "Witches of the Wheel — Lindsay Merbaum"
    assert ev.start_time == datetime(2026, 10, 21, 2, 0, tzinfo=UTC)


def test_description_subtitle_image_location_url():
    ev = _events()[0]
    assert ev.description.startswith("The Life of Harry Hay")
    assert "Who are we?" in ev.description
    assert ev.image_url.startswith("https://www.fabulosabooks.com/uploads/")
    assert ev.location == fabulosa.ADDRESS
    assert ev.url == fabulosa.EVENTS_URL
