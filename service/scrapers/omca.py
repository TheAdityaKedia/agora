"""Oakland Museum of California (OMCA) events scraper.

Friday Nights at OMCA, gallery talks, walks. WordPress + The Events Calendar
→ thin wrapper around scrapers/tribe_events.py. Many events give only
"OMCA campus" as the venue, so that bare name maps to the full address.
"""
from scrapers import tribe_events
from scrapers.base import RawEvent


SOURCE = "museumca.org"
NAME = 'Oakland Museum of California'
SITE_BASE = "https://museumca.org"
EVENTS_URL = "https://museumca.org/"
ADDRESS = 'Oakland Museum of California, 1000 Oak St, Oakland, CA 94607'


def matches(url: str) -> bool:
    return "museumca.org" in url


def _full_address(ev: RawEvent) -> RawEvent:
    if (ev.location or "").strip().lower() in ("omca campus", ""):
        ev.location = ADDRESS
    return ev


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return [_full_address(e) for e in tribe_events.scrape_events(SITE_BASE, fallback_location=ADDRESS)]
