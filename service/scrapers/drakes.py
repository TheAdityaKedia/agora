"""Drake's Brewing events scraper (via The Events Calendar API).

Drake's (San Leandro brewery) lists events for all its taprooms on one
WordPress + The Events Calendar site: trivia, drag bingo and Lotería at
Drake's Dealership (Oakland), live music at the Barrel House (San Leandro),
and lawn movies/markets at The Barn in West Sacramento. Thin wrapper around
scrapers/tribe_events.py; each event's venue block names its taproom, and
The Barn's events are dropped as outside the Bay Area. Weekly series (trivia)
are listed per date far ahead; the pipeline's lookahead window trims them.

Cloudflare fronts the site: a bare "Mozilla/5.0" user agent gets a 403, the
full BROWSER_UA the library sends gets the API.
"""
from scrapers import bay_area, tribe_events
from scrapers.base import RawEvent


SOURCE = "events.drinkdrakes.com"
NAME = "Drake's Brewing"
SITE_BASE = "https://events.drinkdrakes.com"
EVENTS_URL = "https://events.drinkdrakes.com/"
ADDRESS = "Drake's Dealership, 2325 Broadway, Oakland, CA 94612"


def matches(url: str) -> bool:
    return "drinkdrakes.com" in url


def bay_area_only(events: list[RawEvent]) -> list[RawEvent]:
    """Drop events at taprooms outside the Bay Area (The Barn). Pure."""
    return [e for e in events if bay_area.is_bay_area(e.location)]


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return bay_area_only(tribe_events.scrape_events(SITE_BASE, fallback_location=ADDRESS))
