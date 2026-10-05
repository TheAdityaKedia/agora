"""Artists' Television Access (ATA) events scraper.

Mission District artist-run microcinema/gallery (experimental film, Other
Cinema, open screenings). WordPress + The Events Calendar, so a thin wrapper
around scrapers/tribe_events.py; most events carry no venue block, so the
fallback is ATA's own address.
"""
from scrapers import tribe_events
from scrapers.base import RawEvent


SOURCE = "atasite.org"
NAME = "Artists' Television Access"
SITE_BASE = "https://www.atasite.org"
EVENTS_URL = "https://www.atasite.org/"
ADDRESS = "Artists' Television Access, 992 Valencia St, San Francisco, CA 94110"


def matches(url: str) -> bool:
    return "atasite.org" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return tribe_events.scrape_events(SITE_BASE, fallback_location=ADDRESS)
