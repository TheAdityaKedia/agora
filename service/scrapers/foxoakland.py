"""Fox Theater (Oakland) events scraper.

Uptown Oakland's 2,800-capacity concert hall (Another Planet). Thin wrapper
around scrapers/anotherplanet.py.
"""
from scrapers import anotherplanet
from scrapers.base import RawEvent


SOURCE = "thefoxoakland.com"
NAME = 'Fox Theater Oakland'
EVENTS_URL = "https://thefoxoakland.com/"
VENUE = 'Fox Theater, 1807 Telegraph Ave, Oakland, CA 94612'


def matches(url: str) -> bool:
    return "thefoxoakland.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return anotherplanet.scrape_site(EVENTS_URL, venue=VENUE, tag="foxoakland")
