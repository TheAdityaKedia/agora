"""Bird & Beckett Books & Records events scraper (via The Events Calendar API).

Bird & Beckett (a Glen Park bookstore with a busy jazz + poetry series) runs
WordPress with The Events Calendar plugin. Thin wrapper around
scrapers/tribe_events.py, which reads the plugin's REST API (title, UTC start,
permalink, description, venue, image). Only the site base is venue-specific.
"""
from scrapers import tribe_events
from scrapers.base import RawEvent


SOURCE = "birdbeckett.com"
NAME = "Bird & Beckett"
SITE_BASE = "https://birdbeckett.com"
EVENTS_URL = "https://birdbeckett.com/events-calendar/"
ADDRESS = "Bird & Beckett Books & Records, 653 Chenery St, San Francisco, CA 94131"


def matches(url: str) -> bool:
    return "birdbeckett.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return tribe_events.scrape_events(SITE_BASE, fallback_location=ADDRESS)
