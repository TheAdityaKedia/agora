"""Punch Line San Francisco events scraper.

Financial District stand-up club (Live Nation). Thin wrapper around
scrapers/livenation.py (JSON-LD on the /shows page).
"""
from scrapers import livenation
from scrapers.base import RawEvent


SOURCE = "punchlinecomedyclub.com"
NAME = 'Punch Line San Francisco'
EVENTS_URL = "https://www.punchlinecomedyclub.com/shows"
VENUE = 'Punch Line Comedy Club, 444 Battery St, San Francisco, CA 94111'


def matches(url: str) -> bool:
    return "punchlinecomedyclub.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return livenation.scrape_shows(EVENTS_URL, venue=VENUE, tag="punchline")
