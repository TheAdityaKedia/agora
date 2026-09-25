"""Tally Ho! Books events scraper (via the BookManager events API).

Tally Ho! Books (a Piedmont Avenue, Oakland bookstore) runs a BookManager
webstore. Thin wrapper around scrapers/bookmanager.py; only the store id and
site base are venue-specific.
"""
from scrapers import bookmanager
from scrapers.base import RawEvent


SOURCE = "tallyhobookstore.com"
NAME = "Tally Ho! Books"
STORE_ID = 1155461
SITE_BASE = "https://tallyhobookstore.com"
EVENTS_URL = f"{SITE_BASE}/events"
ADDRESS = "Tally Ho! Books, 3941 Piedmont Ave, Oakland, CA 94611"


def matches(url: str) -> bool:
    return "tallyhobookstore.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return bookmanager.scrape_events(STORE_ID, SITE_BASE, fallback_location=ADDRESS, tag="tallyho")
