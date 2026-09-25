"""Russian Hill Bookstore events scraper (via Eventbrite).

Russian Hill Bookstore (Polk St) lists events on a free-form Squarespace page
with no structured data; its RSVP events are on Eventbrite, so this is a thin
wrapper around scrapers/eventbrite.py. Drop-in, no-RSVP events that appear
only on the store page (e.g. letter-writing days) are not captured.
"""
from scrapers import eventbrite
from scrapers.base import RawEvent


SOURCE = "russianhillbookstore.com"
NAME = "Russian Hill Bookstore"
ORGANIZER_URL = "https://www.eventbrite.com/o/russian-hill-bookstore-121459879997"


def matches(url: str) -> bool:
    return "russianhillbookstore.com" in url or "russian-hill-bookstore-121459879997" in url


def scrape(url: str = ORGANIZER_URL) -> list[RawEvent]:
    return eventbrite.scrape_organizer(ORGANIZER_URL)
