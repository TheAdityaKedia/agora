"""Noe Valley Books events scraper.

Noe Valley Books is a neighborhood bookstore with readings, book clubs, and poetry nights. Runs on IndieCommerce; thin wrapper around scrapers/indiecommerce.py.
Only the site base and address are venue-specific.
"""
from scrapers import indiecommerce
from scrapers.base import RawEvent


SOURCE = "noevalleybooks.com"
NAME = "Noe Valley Books"
SITE_BASE = "https://noevalleybooks.com"
EVENTS_URL = "https://noevalleybooks.com/events"
ADDRESS = "Noe Valley Books, 3899 24th St, San Francisco, CA 94114"


def matches(url: str) -> bool:
    return "noevalleybooks.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return indiecommerce.scrape_events(SITE_BASE, fallback_location=ADDRESS, tag="noevalleybooks")
