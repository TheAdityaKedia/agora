from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scrapers import indiecommerce
from scrapers import booksmith, bookpassage, noevalleybooks, mrsdalloways, bookshopwestportal

FIXTURES = Path(__file__).parent / "fixtures"
UTC = ZoneInfo("UTC")
BASE = "https://booksmith.com"


def _listing():
    return indiecommerce.parse_listing((FIXTURES / "indiecommerce_listing.html").read_text(), BASE)


def _detail(name="indiecommerce_detail.html", url="https://bookpassage.com/event/2026-10-13/x"):
    return indiecommerce.parse_event((FIXTURES / name).read_text(), url, fallback_location="Fallback")


# --- listing -----------------------------------------------------------------

def test_listing_reads_only_event_cards():
    items = _listing()
    # 3 cards; the sidebar promo link is not a card and is ignored
    assert len(items) == 3
    assert all("sidebar-promo" not in i.url for i in items)


def test_listing_urls_absolute_including_custom_aliases():
    urls = [i.url for i in _listing()]
    assert urls[0] == "https://booksmith.com/event/2026-09-09/katiebenn"
    assert "https://booksmith.com/event/bunny" in urls  # alias, no date in path


def test_listing_parses_card_date_and_title():
    first = _listing()[0]
    assert first.day == date(2026, 9, 9)
    assert first.title.startswith("Booksmith presents: Katie Benn")


def test_month_path():
    assert indiecommerce.month_path(date(2026, 10, 1)) == "/events/2026/10"


# --- detail ------------------------------------------------------------------

def test_detail_start_time_from_json_ld_to_utc():
    ev = _detail()
    # 2026-10-13T19:00:00-07:00 -> 02:00 UTC next day
    assert ev.start_time == datetime(2026, 10, 14, 2, 0, tzinfo=UTC)


def test_detail_title_url_image():
    ev = _detail()
    assert ev.title == "Yotam Ottolenghi - Ottolenghi Simple Too"
    assert ev.url == "https://bookpassage.com/event/2026-10-13/x"
    assert ev.image_url and ev.image_url.startswith("https://bookpassage.com/")


def test_detail_offsite_location_from_place_block():
    loc = _detail().location
    assert "Dominican University" in loc and "San Rafael" in loc
    assert not loc.startswith("Place")


def test_detail_description_is_body_and_strips_boilerplate():
    desc = _detail().description
    assert desc.startswith("Join Yotam Ottolenghi")
    assert "SUBSCRIBE TO OUR E-NEWSLETTER" not in desc
    assert "SUPPORT OUR EVENTS PROGRAM" not in desc
    assert "****" not in desc
    assert "Subscribe to our newsletter" not in desc  # page footer


def test_detail_falls_back_to_default_location_when_no_place():
    ev = _detail("indiecommerce_detail_instore.html", "https://noevalleybooks.com/event/2026-10-13/poetry-night")
    assert ev.title == "Poetry Night"
    assert ev.location == "Fallback"
    assert ev.start_time == datetime(2026, 10, 14, 2, 0, tzinfo=UTC)
    assert "open mic" in ev.description


def test_detail_without_json_ld_returns_none():
    assert indiecommerce.parse_event("<html><body>nope</body></html>", "u", fallback_location="x") is None


# --- wrappers ----------------------------------------------------------------

def test_wrappers_match_their_domains():
    assert booksmith.matches("https://www.booksmith.com/events")
    assert bookpassage.matches("https://www.bookpassage.com/calendar-author-events")
    assert noevalleybooks.matches("https://noevalleybooks.com/events")
    assert mrsdalloways.matches("https://www.mrsdalloways.com/events")
    assert bookshopwestportal.matches("https://bookshopwestportal.com/events")
    assert not booksmith.matches("https://bookpassage.com/")


def _logistics_first():
    return _detail("indiecommerce_detail_logistics.html", "https://bookpassage.com/event/nicole-nelson-family-language")


def test_description_leads_with_synopsis_heading_not_logistics():
    desc = _logistics_first().description
    # the lead synopsis is an <h6>; the "ABOUT THIS EVENT" block used to come first
    assert desc.startswith("In this striking debut")
    assert "ABOUT THIS EVENT" not in desc


def test_logistics_block_kept_at_the_end():
    desc = _logistics_first().description
    head, _, details = desc.partition("Event details: ")
    assert "Dr. Mona Melamed" in head
    assert "Free Admission" in details and "Free Admission" not in head


def test_offsite_ticket_price_survives_in_event_details():
    assert "$57 Ticket includes pre-signed book" in _detail().description


def test_title_html_entities_decoded():
    assert _logistics_first().title == "Nicole Nelson & Friends - Family Language"
