"""Oakland United Beerworks events scraper.

Brewery taproom near Jack London Square running paint-and-sips, maker
markets and seasonal parties. WordPress + The Events Calendar → thin
wrapper around scrapers/tribe_events.py. The calendar carries no images.
"""
from scrapers import tribe_events
from scrapers.base import RawEvent


SOURCE = "oaklandunitedbeerworks.com"
NAME = "Oakland United Beerworks"
SITE_BASE = "https://oaklandunitedbeerworks.com"
EVENTS_URL = "https://oaklandunitedbeerworks.com/events/"
ADDRESS = "Oakland United Beerworks, 262 2nd St, Oakland, CA 94607"


def matches(url: str) -> bool:
    return "oaklandunitedbeerworks.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return tribe_events.scrape_events(SITE_BASE, fallback_location=ADDRESS)
