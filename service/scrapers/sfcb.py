"""San Francisco Center for the Book events scraper.

Potrero Hill book-arts center: exhibition openings, artist talks, readings
and letterpress/bookbinding events. Squarespace Events Collection → thin
wrapper around scrapers/squarespace_events.py; about half the cards have no
excerpt, so descriptions come from the detail pages.
"""
from scrapers import squarespace_events
from scrapers.base import RawEvent


SOURCE = "sfcb.org"
NAME = 'San Francisco Center for the Book'
CALENDAR_URL = "https://www.sfcb.org/calendar"
ADDRESS = 'San Francisco Center for the Book, 375 Rhode Island St, San Francisco, CA 94103'


def matches(url: str) -> bool:
    return "sfcb.org" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return squarespace_events.scrape_collection(
        CALENDAR_URL, fallback_location=ADDRESS, enrich_descriptions=True, upcoming_only=True)
