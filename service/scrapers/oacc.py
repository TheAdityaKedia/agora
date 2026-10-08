"""Oakland Asian Cultural Center (OACC) events scraper.

OACC (Pacific Renaissance Plaza, Oakland Chinatown) hosts talks, author
conversations, workshops and community gatherings. WordPress + The Events
Calendar → thin wrapper around scrapers/tribe_events.py. A handful of events
at a time; each carries its own venue block, image and description.
"""
from scrapers import tribe_events
from scrapers.base import RawEvent


SOURCE = "oacc.cc"
NAME = "Oakland Asian Cultural Center"
SITE_BASE = "https://oacc.cc"
EVENTS_URL = "https://oacc.cc/"
ADDRESS = "Oakland Asian Cultural Center, 388 9th St #290, Oakland, CA 94607"


def matches(url: str) -> bool:
    return "oacc.cc" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return tribe_events.scrape_events(SITE_BASE, fallback_location=ADDRESS)
