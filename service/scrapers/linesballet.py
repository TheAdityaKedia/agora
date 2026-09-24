"""Alonzo King LINES Ballet events scraper (via The Events Calendar / Tribe API).

LINES Ballet runs WordPress with The Events Calendar, so it reuses
scrapers/tribe_events.py. It's a touring company, so its calendar spans the
whole country — we keep only Bay Area dates (Davies Symphony Hall, Dominican
University, etc.) via scrapers/bay_area.py.
"""
from scrapers import bay_area, tribe_events
from scrapers.base import RawEvent


SOURCE = "linesballet.org"
NAME = "Alonzo King LINES Ballet"
SITE_BASE = "https://linesballet.org"
AREA = "San Francisco Bay Area"


def matches(url: str) -> bool:
    return "linesballet.org" in url


def scrape(url: str = SITE_BASE) -> list[RawEvent]:
    events = tribe_events.scrape_events(SITE_BASE, fallback_location=AREA)
    return [e for e in events if bay_area.is_bay_area(e.location)]
