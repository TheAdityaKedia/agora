"""FACT/SF events scraper (via its Squarespace events collection).

FACT/SF (a contemporary dance company / studio in SF) publishes its calendar as
a Squarespace Events Collection. Thin wrapper around
scrapers/squarespace_events.py.
"""
from scrapers import squarespace_events
from scrapers.base import RawEvent


SOURCE = "factsf.org"
NAME = "FACT/SF"
CALENDAR_URL = "https://factsf.org/events"
ADDRESS = "FACT/SF, San Francisco"


def matches(url: str) -> bool:
    return "factsf.org" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return squarespace_events.scrape_collection(CALENDAR_URL, fallback_location=ADDRESS)
