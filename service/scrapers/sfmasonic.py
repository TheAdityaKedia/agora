"""The Masonic (SF Masonic Auditorium) events scraper.

Nob Hill's 3,300-seat Live Nation hall: touring concerts, comedy, talks.
Thin wrapper around scrapers/livenation.py (JSON-LD on the /shows page).
"""
from scrapers import livenation
from scrapers.base import RawEvent


SOURCE = "sfmasonic.com"
NAME = 'The Masonic'
EVENTS_URL = "https://www.sfmasonic.com/shows"
VENUE = 'The Masonic, 1111 California St, San Francisco, CA 94108'


def matches(url: str) -> bool:
    return "sfmasonic.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return livenation.scrape_shows(EVENTS_URL, venue=VENUE, tag="sfmasonic")
