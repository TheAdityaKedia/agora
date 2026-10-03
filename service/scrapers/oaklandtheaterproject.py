"""Oakland Theater Project events scraper (via OvationTix).

Oakland Theater Project (formerly Ubuntu Theater Project) produces plays at
FLAX art & design's theater in downtown Oakland. Its Squarespace site links
ticketing to OvationTix client 35459, so this is a thin wrapper around
scrapers/ovationtix.py (one event per performance).
"""
from scrapers import ovationtix
from scrapers.base import RawEvent


SOURCE = "oaklandtheaterproject.org"
NAME = "Oakland Theater Project"
CLIENT_ID = "35459"
EVENTS_URL = "https://oaklandtheaterproject.org/"
ADDRESS = "Oakland Theater Project at FLAX art & design, 1501 Martin Luther King Jr Way, Oakland, CA 94612"


def matches(url: str) -> bool:
    return "oaklandtheaterproject.org" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return ovationtix.scrape_client(CLIENT_ID, fallback_location=ADDRESS, tag="oaklandtheaterproject")
