"""Medicine for Nightmares events scraper (via its Squarespace events collection).

Medicine for Nightmares (a Mission bookstore/gallery with readings, film, and
music series) publishes its calendar as a Squarespace Events Collection. Thin
wrapper around scrapers/squarespace_events.py. Only the calendar URL is
venue-specific.
"""
from scrapers import squarespace_events
from scrapers.base import RawEvent


SOURCE = "medicinefornightmares.com"
NAME = "Medicine for Nightmares"
CALENDAR_URL = "https://medicinefornightmares.com/events"
ADDRESS = "Medicine for Nightmares, 3036 24th St, San Francisco, CA 94110"


def matches(url: str) -> bool:
    return "medicinefornightmares.com" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return squarespace_events.scrape_collection(CALENDAR_URL, fallback_location=ADDRESS)
