"""Bookshop West Portal events scraper.

Bookshop West Portal is a neighborhood bookstore with author events, book clubs, and story time. Runs on IndieCommerce; thin wrapper around scrapers/indiecommerce.py.
Only the site base and address are venue-specific.
"""
from scrapers import indiecommerce
from scrapers.base import RawEvent


SOURCE = "bookshopwestportal.com"
NAME = "Bookshop West Portal"
SITE_BASE = "https://bookshopwestportal.com"
EVENTS_URL = "https://bookshopwestportal.com/events"
ADDRESS = "Bookshop West Portal, 80 West Portal Ave, San Francisco, CA 94127"


def matches(url: str) -> bool:
    return "bookshopwestportal.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return indiecommerce.scrape_events(SITE_BASE, fallback_location=ADDRESS, tag="bookshopwestportal")
