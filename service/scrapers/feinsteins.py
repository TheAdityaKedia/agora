"""Feinstein's at the Nikko events scraper.

Cabaret and supper club in Hotel Nikko: Broadway and jazz singers, tribute
shows, open mics and Sunday brunch concerts. TicketWeb WordPress plugin →
thin wrapper around scrapers/ticketweb.py.
"""
from scrapers import ticketweb
from scrapers.base import RawEvent


SOURCE = "feinsteinssf.com"
NAME = "Feinstein's at the Nikko"
SITE_BASE = "https://www.feinsteinssf.com"
EVENTS_URL = "https://www.feinsteinssf.com/events/"
VENUE = "Feinstein's at the Nikko, 222 Mason St, San Francisco, CA 94102"


def matches(url: str) -> bool:
    return "feinsteinssf.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return ticketweb.scrape_site(SITE_BASE, venue=VENUE, tag="feinsteins")
