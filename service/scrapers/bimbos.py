"""Bimbo's 365 Club events scraper.

North Beach's 1931 supper-club ballroom, booked by Another Planet for
concerts. TicketWeb WordPress plugin → thin wrapper around
scrapers/ticketweb.py. Descriptions are mostly just the openers; the site
has little more.
"""
from scrapers import ticketweb
from scrapers.base import RawEvent


SOURCE = "bimbos365club.com"
NAME = "Bimbo's 365 Club"
SITE_BASE = "https://bimbos365club.com"
EVENTS_URL = "https://bimbos365club.com/events/"
VENUE = "Bimbo's 365 Club, 1025 Columbus Ave, San Francisco, CA 94133"


def matches(url: str) -> bool:
    return "bimbos365club.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return ticketweb.scrape_site(SITE_BASE, venue=VENUE, tag="bimbos")
