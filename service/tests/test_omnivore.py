import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scrapers import omnivore
from scrapers.datetext import parse_weekday_date

FIXTURES = Path(__file__).parent / "fixtures"
UTC = ZoneInfo("UTC")


def _products():
    return json.loads((FIXTURES / "omnivore_products.json").read_text())["products"]


def test_reads_date_line_from_product_page():
    html = (FIXTURES / "omnivore_product.html").read_text()
    assert omnivore.parse_date_line(html) == "Thursday, October 8 at 6:30 pm"


def test_parse_when_uses_weekday_to_pick_year():
    today = date(2026, 9, 24)
    # Oct 8 2026 is a Thursday
    assert parse_weekday_date("Thursday, October 8 at 6:30 pm", today) == datetime(2026, 10, 9, 1, 30, tzinfo=UTC)
    # Feb 11 is a Thursday in 2027, not 2026
    assert parse_weekday_date("Thursday, February 11 at 6:30 pm", today).year == 2027


def test_parse_when_handles_caps_and_ordinals():
    today = date(2026, 9, 24)
    assert parse_weekday_date("WEDNESDAY, OCTOBER 7 AT 1:00 PM", today) == datetime(2026, 10, 7, 20, 0, tzinfo=UTC)
    assert parse_weekday_date("Monday, November 16th at 6:30 pm", today) == datetime(2026, 11, 17, 2, 30, tzinfo=UTC)
    assert parse_weekday_date("Coming soon", today) is None


def test_event_from_product_in_store():
    p = next(p for p in _products() if p["handle"].startswith("nik-sharma"))
    ev = omnivore.event_from_product(p, datetime(2026, 10, 9, 1, 30, tzinfo=UTC))
    assert ev.title.startswith("Nik Sharma Author Talk")
    assert ev.url == f"https://omnivorebooks.myshopify.com/products/{p['handle']}"
    assert ev.location == omnivore.ADDRESS
    assert ev.description and "<p" not in ev.description
    assert ev.image_url and ev.image_url.startswith("https://")


def test_event_from_product_off_site_does_not_claim_the_shop():
    p = next(p for p in _products() if p["handle"].startswith("off-site"))
    ev = omnivore.event_from_product(p, datetime(2026, 10, 6, 2, 0, tzinfo=UTC))
    assert ev.location != omnivore.ADDRESS
    assert "off-site" in ev.location.lower()


def test_matches():
    assert omnivore.matches("https://omnivorebooks.myshopify.com/collections/upcoming-events")
    assert omnivore.matches("https://www.omnivorebooks.com/")
