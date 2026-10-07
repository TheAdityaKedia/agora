"""Diaspora Arts Connection events scraper.

Bay Area nonprofit presenting diaspora artists — Persian, Afghan and other
immigrant-community music, theater and film — at venues around the region
(YBCA, San Jose's Montgomery Theater…) and occasionally elsewhere in
California. Squarespace events collection read through its ``?format=json``
feed (scrapers/squarespace_events.py::scrape_json), which carries each
event's own venue address; events outside the Bay Area are dropped
(scrapers/bay_area.py).
"""
from scrapers import bay_area, squarespace_events
from scrapers.base import RawEvent


SOURCE = "diasporaartsconnection.org"
NAME = "Diaspora Arts Connection"
CALENDAR_URL = "https://www.diasporaartsconnection.org/events"


def matches(url: str) -> bool:
    return "diasporaartsconnection.org" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    events = squarespace_events.scrape_json(CALENDAR_URL)
    return [e for e in events if bay_area.is_bay_area(e.location)]
