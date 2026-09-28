from datetime import datetime, timezone
from pathlib import Path

from scrapers import readingrhythms

FIXTURES = Path(__file__).parent / "fixtures"
EVENTS_HTML = (FIXTURES / "readingrhythms_events.html").read_text()
EVENT_HTML = (FIXTURES / "readingrhythms_event.html").read_text()
SF_URL = ("https://readingrhythms.co/events/san-francisco/reading-rhythms-san-francisco-"
          "sense-sensibility-a-season-for-sisters-reading-party-oct-11-f40fdd62")


def test_matches_new_site_not_old_luma_calendar():
    assert readingrhythms.matches("https://readingrhythms.co/events")
    assert not readingrhythms.matches("https://luma.com/readingrhythms-ca")
    assert not readingrhythms.matches("https://luma.com/thecommons")


def test_parse_listing_keeps_only_bay_area_city_links():
    # fixture: SF, LA, Toronto, San Diego event cards + nav noise
    assert readingrhythms.parse_listing(EVENTS_HTML) == [SF_URL]


def test_parse_event_maps_json_ld():
    raw = readingrhythms.parse_event(EVENT_HTML, SF_URL)
    assert raw.title == ("Reading Rhythms San Francisco: Sense & Sensibility: "
                         "A Season for Sisters Reading Party")
    assert raw.start_time == datetime(2026, 10, 11, 2, 45, tzinfo=timezone.utc)
    assert raw.location == "The Love Potion Library, 284 Noe St, San Francisco, CA 94114"
    assert raw.url == SF_URL  # the info page, not the Luma checkout
    assert raw.image_url.startswith("https://images.lumacdn.com/")


def test_parse_event_repairs_double_encoded_description():
    desc = readingrhythms.parse_event(EVENT_HTML, SF_URL).description
    assert "â€" not in desc and "â" not in desc and "​" not in desc
    assert desc.startswith("No one knows your heart like a sister.")
    assert "##" not in desc and "*" not in desc  # markdown markers stripped


def test_parse_event_drops_non_bay_area_location():
    html = EVENT_HTML.replace("284 Noe St, San Francisco, CA 94114", "123 Main St, Los Angeles, CA 90012")
    assert readingrhythms.parse_event(html, SF_URL) is None


def test_parse_event_without_json_ld_returns_none():
    assert readingrhythms.parse_event("<html><body>nothing</body></html>", SF_URL) is None


def test_scrape_fetches_only_bay_area_event_pages(monkeypatch):
    fetched = []

    def fake_get(url):
        fetched.append(url)
        return EVENTS_HTML if url == readingrhythms.EVENTS_URL else EVENT_HTML

    monkeypatch.setattr(readingrhythms, "_get", fake_get)
    events = readingrhythms.scrape()
    assert fetched == [readingrhythms.EVENTS_URL, SF_URL]
    assert [e.url for e in events] == [SF_URL]
