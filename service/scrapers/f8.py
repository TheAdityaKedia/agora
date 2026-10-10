"""F8 (feightsf.com) events scraper.

SoMa nightclub at 1192 Folsom St: house, techno, darkwave and community dance
parties, most nights. Its Squarespace events collection is read through the
``?format=json`` feed (scrapers/squarespace_events.py::scrape_json): the HTML
list's cards all parse as midnight on this theme, while the feed has exact
start times (most nights 9–10pm), excerpts and posters.
"""
from scrapers import squarespace_events
from scrapers.base import RawEvent


SOURCE = "feightsf.com"
NAME = "F8"
CALENDAR_URL = "https://www.feightsf.com/new-events"
ADDRESS = "F8, 1192 Folsom St, San Francisco, CA 94103"


def matches(url: str) -> bool:
    return "feightsf.com" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return squarespace_events.scrape_json(CALENDAR_URL, fallback_location=ADDRESS)
