"""Jewish Community Center of San Francisco (JCCSF) events scraper.

Kanbar Hall talks, author events, music, film and community programs.
WordPress + The Events Calendar → thin wrapper around
scrapers/tribe_events.py.
"""
from scrapers import tribe_events
from scrapers.base import RawEvent


SOURCE = "jccsf.org"
NAME = 'JCCSF'
SITE_BASE = "https://www.jccsf.org"
EVENTS_URL = "https://www.jccsf.org/"
ADDRESS = 'JCCSF, 3200 California St, San Francisco, CA 94118'


def matches(url: str) -> bool:
    return "jccsf.org" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return tribe_events.scrape_events(SITE_BASE, fallback_location=ADDRESS)
