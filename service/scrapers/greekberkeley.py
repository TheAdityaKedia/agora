"""Greek Theatre (Berkeley) events scraper.

UC Berkeley's 8,500-seat outdoor amphitheater, booked by Another Planet
(spring–fall concert season). Thin wrapper around scrapers/anotherplanet.py.
"""
from scrapers import anotherplanet
from scrapers.base import RawEvent


SOURCE = "thegreekberkeley.com"
NAME = 'The Greek Theatre Berkeley'
EVENTS_URL = "https://thegreekberkeley.com/"
VENUE = 'Greek Theatre, 2001 Gayley Rd, Berkeley, CA 94720'


def matches(url: str) -> bool:
    return "thegreekberkeley.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return anotherplanet.scrape_site(EVENTS_URL, venue=VENUE, tag="greekberkeley")
