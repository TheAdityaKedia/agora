"""San Francisco Neo-Futurists events scraper (via Eventbrite).

Thin wrapper — all the work lives in scrapers/eventbrite.py.
"""
from scrapers import eventbrite
from scrapers.base import RawEvent


SOURCE = "sfneofuturists.org"
NAME = "SF Neo-Futurists"
ORGANIZER_URL = "https://www.eventbrite.com/o/san-francisco-neo-futurists-6706701567"


def matches(url: str) -> bool:
    return "san-francisco-neo-futurists-6706701567" in url or "sfneofuturists.org" in url


def scrape(url: str = ORGANIZER_URL) -> list[RawEvent]:
    return eventbrite.scrape_organizer(url)
