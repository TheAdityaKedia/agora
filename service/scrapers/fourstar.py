"""4-Star Theater events scraper (via Veezi).

The 4-Star (a CinemaSF neighborhood cinema in the Richmond) embeds a Veezi
sessions widget. Thin wrapper around scrapers/veezi.py — see that module and
scrapers/balboa.py. Only the Veezi site token is venue-specific.
"""
from scrapers import veezi
from scrapers.base import RawEvent


SOURCE = "4-star-movies.com"
NAME = "4-Star Theater"
SITE_TOKEN = "d2atbcege5knqsavntt91g1250"
CALENDAR_URL = "https://www.4-star-movies.com/calendar-of-events"
ADDRESS = "4-Star Theater, 2200 Clement St, San Francisco, CA 94121"


def matches(url: str) -> bool:
    return "4-star-movies.com" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return veezi.scrape_sessions(SITE_TOKEN, fallback_location=ADDRESS)
