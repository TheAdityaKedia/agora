"""Phoenix Theater events scraper (via Eventbrite).

The Phoenix Theater publishes its calendar on Eventbrite. This scraper is a
thin wrapper around scrapers/eventbrite.py — the heavy lifting (rendering
the organizer page, fetching each event's JSON-LD, mapping to RawEvent) lives
there so future venues can reuse it (see docstring in eventbrite.py).

Note: This is the Phoenix Theater in Petaluma (SF Bay Area), not the SF
Phoenix Theatre (phoenixtheatresf.org, which publishes no scrapable event
data). Their organizer URL is the source of truth linked directly from
their events.
"""
from scrapers import eventbrite
from scrapers.base import RawEvent


SOURCE = "phoenixtheater.com"
NAME = "Phoenix Theater (Petaluma)"
ORGANIZER_URL = "https://www.eventbrite.com/o/phoenix-theater-26319831111"


def matches(url: str) -> bool:
    return "phoenix-theater-26319831111" in url or "phoenixtheater.com" in url


def scrape(url: str = ORGANIZER_URL) -> list[RawEvent]:
    return eventbrite.scrape_organizer(url)
