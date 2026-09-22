"""Big Brain Lectures — Bay Area (via Luma).

A thin wrapper around scrapers/luma.py; the shared library handles fetching
the calendar page, parsing its JSON-LD ItemList, mapping each Event to a
RawEvent, and enriching descriptions from detail pages. Future Luma-hosted
organizers can add a similar 3-line wrapper.
"""
from scrapers import luma
from scrapers.base import RawEvent


SOURCE = "luma.com"
NAME = "Big Brain Lectures — Bay Area"
CALENDAR_URL = "https://luma.com/Big-Brain-Bay"


def matches(url: str) -> bool:
    return "luma.com/Big-Brain-Bay" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return luma.scrape_calendar(url)
