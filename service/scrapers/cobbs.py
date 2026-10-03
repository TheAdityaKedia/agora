"""Cobb's Comedy Club events scraper.

North Beach stand-up club (Live Nation). Thin wrapper around
scrapers/livenation.py (JSON-LD on the /shows page; typed MusicEvent even
though every show is comedy — the venue profile steers the tagger).
"""
from scrapers import livenation
from scrapers.base import RawEvent


SOURCE = "cobbscomedy.com"
NAME = "Cobb's Comedy Club"
EVENTS_URL = "https://www.cobbscomedy.com/shows"
VENUE = "Cobb's Comedy Club, 915 Columbus Ave, San Francisco, CA 94133"


def matches(url: str) -> bool:
    return "cobbscomedy.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return livenation.scrape_shows(EVENTS_URL, venue=VENUE, tag="cobbs")
