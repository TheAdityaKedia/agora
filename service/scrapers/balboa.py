"""Balboa Theatre events scraper (via its Squarespace events collection).

The Balboa (a CinemaSF neighborhood cinema in the Richmond) publishes its
showtimes as a Squarespace Events Collection on its own site. Thin wrapper
around scrapers/squarespace_events.py, which parses the rendered event list
(title, showtime, rich synopsis, venue-hosted permalink, poster). Only the
calendar URL is venue-specific.
"""
from scrapers import squarespace_events
from scrapers.base import RawEvent


SOURCE = "balboamovies.com"
NAME = "Balboa Theatre"
CALENDAR_URL = "https://www.balboamovies.com/calendar-of-events"
ADDRESS = "Balboa Theatre, 3630 Balboa St, San Francisco, CA 94121"


def matches(url: str) -> bool:
    return "balboamovies.com" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return squarespace_events.scrape_collection(CALENDAR_URL, fallback_location=ADDRESS)
