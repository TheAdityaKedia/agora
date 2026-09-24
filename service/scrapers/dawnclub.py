"""The Dawn Club events scraper (via its Squarespace events collection).

The Dawn Club (a downtown SF jazz club) publishes its shows as a Squarespace
Events Collection on its /music page — a server-rendered list of
``article.eventlist-event`` cards, each carrying the act, showtime, a rich
artist bio/synopsis, a venue-hosted permalink, and a poster. Thin wrapper
around scrapers/squarespace_events.py; see that module and scrapers/balboa.py.
Only the collection URL is venue-specific.
"""
from scrapers import squarespace_events
from scrapers.base import RawEvent


SOURCE = "dawnclub.com"
NAME = "The Dawn Club"
CALENDAR_URL = "https://www.dawnclub.com/music"
ADDRESS = "The Dawn Club, San Francisco"


def matches(url: str) -> bool:
    return "dawnclub.com" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return squarespace_events.scrape_collection(CALENDAR_URL, fallback_location=ADDRESS)
