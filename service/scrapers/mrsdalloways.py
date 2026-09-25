"""Mrs. Dalloway's events scraper.

Mrs. Dalloway's Literary & Garden Arts (Berkeley, Elmwood) hosts author readings and book launches. Runs on IndieCommerce; thin wrapper around scrapers/indiecommerce.py.
Only the site base and address are venue-specific.
"""
from scrapers import indiecommerce
from scrapers.base import RawEvent


SOURCE = "mrsdalloways.com"
NAME = "Mrs. Dalloway's"
SITE_BASE = "https://mrsdalloways.com"
EVENTS_URL = "https://mrsdalloways.com/events"
ADDRESS = "Mrs. Dalloway's, 2904 College Ave, Berkeley, CA 94705"


def matches(url: str) -> bool:
    return "mrsdalloways.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return indiecommerce.scrape_events(SITE_BASE, fallback_location=ADDRESS, tag="mrsdalloways")
