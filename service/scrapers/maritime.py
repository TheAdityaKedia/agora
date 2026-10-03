"""San Francisco Maritime National Park Association events scraper.

The nonprofit behind the USS Pampanito and the Hyde Street Pier programs:
chantey sings, Fleet Week viewing, the Maritime Ball. Squarespace Events
Collection → thin wrapper around scrapers/squarespace_events.py; cards carry
no excerpt, so descriptions come from the detail pages.
"""
from scrapers import squarespace_events
from scrapers.base import RawEvent


SOURCE = "maritime.org"
NAME = 'San Francisco Maritime National Park Association'
CALENDAR_URL = "https://maritime.org/events"
ADDRESS = 'San Francisco Maritime National Park Association, Pier 45, San Francisco, CA 94133'


def matches(url: str) -> bool:
    return "maritime.org" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return squarespace_events.scrape_collection(
        CALENDAR_URL, fallback_location=ADDRESS, enrich_descriptions=True, card_address=True)
