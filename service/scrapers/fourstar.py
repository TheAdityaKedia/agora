"""4-Star Theater events scraper (via its Squarespace events collection).

The 4-Star (a CinemaSF neighborhood cinema in the Richmond) publishes its
programming — film sessions plus live-music nights — as a Squarespace Events
Collection on its own site. Thin wrapper around scrapers/squarespace_events.py;
see that module and scrapers/balboa.py. Only the calendar URL is venue-specific.
"""
from scrapers import squarespace_events
from scrapers.base import RawEvent


SOURCE = "4-star-movies.com"
NAME = "4-Star Theater"
CALENDAR_URL = "https://www.4-star-movies.com/calendar-of-events"
ADDRESS = "4-Star Theater, 2200 Clement St, San Francisco, CA 94121"


def matches(url: str) -> bool:
    return "4-star-movies.com" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return squarespace_events.scrape_collection(CALENDAR_URL, fallback_location=ADDRESS)
