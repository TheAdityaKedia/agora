"""San Francisco Center for the Book events scraper.

Potrero Hill book-arts center: exhibition openings, artist talks, readings
and letterpress/bookbinding events. Squarespace Events Collection → thin
wrapper around scrapers/squarespace_events.py; about half the cards have no
excerpt, so descriptions come from the detail pages. A page whose only text
is a "Source: <link>" credit gets no description rather than that line.
"""
import re

from scrapers import squarespace_events
from scrapers.base import RawEvent


SOURCE = "sfcb.org"
NAME = 'San Francisco Center for the Book'
CALENDAR_URL = "https://www.sfcb.org/calendar"
ADDRESS = 'San Francisco Center for the Book, 375 Rhode Island St, San Francisco, CA 94103'


_SOURCE_ONLY_RE = re.compile(r"^Source:\s*\S+$", re.I)


def matches(url: str) -> bool:
    return "sfcb.org" in url


def drop_source_only(events: list[RawEvent]) -> list[RawEvent]:
    """Blank descriptions that are only a "Source: <url>" credit. Pure."""
    for e in events:
        if e.description and _SOURCE_ONLY_RE.match(e.description.strip()):
            e.description = None
    return events


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return drop_source_only(squarespace_events.scrape_collection(
        CALENDAR_URL, fallback_location=ADDRESS, enrich_descriptions=True, upcoming_only=True))
