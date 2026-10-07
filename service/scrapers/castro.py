"""The Castro Theatre events scraper.

The Castro's 1922 movie palace, reopened by Another Planet as a concert,
comedy and film venue. Thin wrapper around scrapers/anotherplanet.py.
"""
from scrapers import anotherplanet
from scrapers.base import RawEvent


SOURCE = "thecastro.com"
NAME = 'The Castro Theatre'
EVENTS_URL = "https://thecastro.com/"
VENUE = 'The Castro Theatre, 429 Castro St, San Francisco, CA 94114'


def matches(url: str) -> bool:
    return "thecastro.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return anotherplanet.scrape_site(EVENTS_URL, venue=VENUE, tag="castro")
