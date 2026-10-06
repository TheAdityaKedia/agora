import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapers import masala, tugoz

FIXTURES = Path(__file__).parent / "fixtures"
# fixture feeds hold one show before this and the rest after it
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _feed(event_id):
    return json.loads((FIXTURES / f"tugoz_{event_id}.json").read_text())


@pytest.fixture
def lt10_page():
    return masala.parse_series_page((FIXTURES / "masala_laugh_ticket.html").read_text())


def test_matches():
    assert masala.matches("https://masalacc.org/tickets")
    assert not masala.matches("https://www.berkeleyalembic.org/events-2")


def test_live_event_ids_from_config():
    assert masala.live_event_ids((FIXTURES / "masala_config.js").read_text()) == [113920, 112933]
    assert masala.live_event_ids("const SITE_CONFIG = {};") == []


def test_tugoz_upcoming_shows_ignore_stale_ispast():
    # every fixture show still says ispast: 0 (stale CDN copy); the Aug 28 one is past
    shows = tugoz.upcoming_shows(_feed(113920), now=NOW)
    assert [s["eventid"] for s in shows] == [113939, 113920]
    assert tugoz.show_title(shows[1]) == "Laugh Ticket 10 — Danville"
    assert tugoz.show_location(shows[1]) == "Village Theatre, 233 Front St, Danville, CA 94526"


def test_parse_series_page(lt10_page):
    assert lt10_page["blurb"].startswith("Laugh Ticket 10 — the next desi & Hinglish")
    assert lt10_page["lineups"][113920].startswith("Akash, Ankit, Aryan")
    assert lt10_page["prices"][datetime(2026, 10, 18, 23, 0, tzinfo=timezone.utc)] == [29, 34, 39]


def test_events_from_feed(lt10_page):
    events = masala.events_from_feed(_feed(113920), lt10_page, now=NOW)
    danville = events[1]
    assert danville.title == "Masala Comedy Club: Laugh Ticket 10 — Danville"
    assert danville.start_time == datetime(2026, 10, 18, 23, 0, tzinfo=timezone.utc)
    assert danville.url == "https://masalacc.org/laugh-ticket-10/?eid=113920"
    assert "Comics: Akash, Ankit" in danville.description
    assert danville.description.endswith("Tickets: $29 / $34 / $39")
    assert danville.image_url.startswith("https://static.tugoz.com/")


def test_open_mic_without_lineup_or_prices():
    page = masala.parse_series_page((FIXTURES / "masala_open_mic.html").read_text())
    events = masala.events_from_feed(_feed(112933), page, now=NOW)
    assert [e.title for e in events] == ["Masala Comedy Club: Open Mic"] * 2
    assert events[0].location == "India Community Center, 525 Los Coches St, Milpitas, CA 95035"
    assert events[0].description == page["blurb"]
