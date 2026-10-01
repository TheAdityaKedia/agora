from datetime import datetime, timezone
from pathlib import Path

from scrapers import faight

HTML = (Path(__file__).parent / "fixtures" / "faight_events.html").read_text()
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def test_matches_events_page_only():
    assert faight.matches("https://www.thefaight.com/events")
    assert not faight.matches("https://partiful.com/explore/sf")


def test_parse_skips_private_events():
    titles = [e.title for e in faight.parse(HTML, now=NOW)]
    assert "Private Booking" not in titles
    assert titles == ["Proxima Parada ft. Zoë Winter", "Blue Lemonade - Live @ The Faight", "Open Mic"]


def test_event_fields():
    e = faight.parse(HTML, now=NOW)[1]
    assert e.start_time == datetime(2026, 10, 3, 2, 0, tzinfo=timezone.utc)  # Fri Oct 2, 7pm PT
    assert e.location == "The Faight Collective, 475 Haight St, San Francisco, CA 94117"
    assert e.url == "https://www.eventbrite.com/e/blue-lemonade-live-the-faight-tickets-2001901486235"
    assert e.description.startswith("Blue Lemonade live at The Faight")
    assert e.image_url == ("https://cdn.sanity.io/images/3l1powkg/production/"
                           "6b4c2796390e6bdefb0edc84ff7b335db4d68e72-2000x1336.jpg")


def test_tracking_params_stripped_and_missing_ticket_link_falls_back_to_the_card():
    events = faight.parse(HTML, now=NOW)
    assert all("aff=" not in (e.url or "") for e in events)
    assert events[2].url == "https://www.thefaight.com/events#open-mic"


def test_past_events_dropped():
    assert faight.parse(HTML, now=datetime(2026, 10, 6, tzinfo=timezone.utc))[0].title == "Open Mic"


def test_page_without_data_returns_nothing():
    assert faight.parse("<html><body>maintenance</body></html>", now=NOW) == []
