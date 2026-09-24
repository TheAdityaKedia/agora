"""The Marsh events scraper (via its Ludus ticketing calendar).

The Marsh (SF Mission + Berkeley solo-performance / theater venue) sells tickets
through Ludus. Thin wrapper around scrapers/ludus.py; the calendar's per-show
category ("San Francisco" / "Berkeley") becomes the event location.
"""
from scrapers import ludus
from scrapers.base import RawEvent


SOURCE = "themarsh.org"
NAME = "The Marsh"
CALENDAR_URL = "https://themarsh.ludus.com/calendar"
FALLBACK_LOCATION = "The Marsh, San Francisco / Berkeley"


def matches(url: str) -> bool:
    return "themarsh.org" in url or "themarsh.ludus.com" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return ludus.scrape_calendar(CALENDAR_URL, fallback_location=FALLBACK_LOCATION)
