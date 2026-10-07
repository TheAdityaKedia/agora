"""August Hall events scraper.

Union Square music venue (concerts, DJ nights). TicketWeb WordPress plugin
→ thin wrapper around scrapers/ticketweb.py. Its show pages carry no
descriptions (checked 2026-10), only title, date, time and poster.
"""
from scrapers import ticketweb
from scrapers.base import RawEvent


SOURCE = "augusthallsf.com"
NAME = 'August Hall'
SITE_BASE = "https://www.augusthallsf.com"
EVENTS_URL = "https://www.augusthallsf.com/events/"
VENUE = 'August Hall, 420 Mason St, San Francisco, CA 94102'


def matches(url: str) -> bool:
    return "augusthallsf.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return ticketweb.scrape_site(SITE_BASE, venue=VENUE, tag="augusthall")
