"""The Commons (SF) events — via Luma.

A thin wrapper around scrapers/luma.py (see bigbrainbay.py for the pattern).
The Commons is an SF membership community/club that publishes its member events
on a Luma calendar.
"""
from scrapers import luma
from scrapers.base import RawEvent


SOURCE = "luma.com"
NAME = "The Commons (SF)"
CALENDAR_URL = "https://luma.com/thecommons"


def matches(url: str) -> bool:
    return "luma.com/thecommons" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return luma.scrape_calendar(url)
