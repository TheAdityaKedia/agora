"""Book Passage events scraper.

Book Passage (Corte Madera + SF Ferry Building) runs a large author-event and class calendar; off-site events carry their own venue, so the address is only a fallback. Runs on IndieCommerce; thin wrapper around scrapers/indiecommerce.py.
Only the site base and address are venue-specific.
"""
from scrapers import indiecommerce
from scrapers.base import RawEvent


SOURCE = "bookpassage.com"
NAME = "Book Passage"
SITE_BASE = "https://bookpassage.com"
EVENTS_URL = "https://bookpassage.com/events"
ADDRESS = "Book Passage, 51 Tamal Vista Blvd, Corte Madera, CA 94925"


def matches(url: str) -> bool:
    return "bookpassage.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return indiecommerce.scrape_events(SITE_BASE, fallback_location=ADDRESS, tag="bookpassage")
