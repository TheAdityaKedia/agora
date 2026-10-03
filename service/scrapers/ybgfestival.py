"""Yerba Buena Gardens Festival events scraper.

Free outdoor performances (music, dance, family) in Yerba Buena Gardens,
May–Oct. Distinct from YBCA. WordPress + The Events Calendar → thin wrapper
around scrapers/tribe_events.py (venue blocks name the garden/lawn).
"""
from scrapers import tribe_events
from scrapers.base import RawEvent


SOURCE = "ybgfestival.org"
NAME = 'Yerba Buena Gardens Festival'
SITE_BASE = "https://ybgfestival.org"
EVENTS_URL = "https://ybgfestival.org/"
ADDRESS = 'Yerba Buena Gardens, Mission St between 3rd & 4th Sts, San Francisco, CA 94103'


def matches(url: str) -> bool:
    return "ybgfestival.org" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return tribe_events.scrape_events(SITE_BASE, fallback_location=ADDRESS)
