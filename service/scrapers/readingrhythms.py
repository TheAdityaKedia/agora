"""Reading Rhythms California events — via Luma.

A thin wrapper around scrapers/luma.py (see bigbrainbay.py for the pattern).
Reading Rhythms is a "reading party" series — read with friends to live music
and curated playlists. The California calendar is statewide (San Francisco, LA,
San Diego, …), so we keep only Bay Area events (scrapers/bay_area.py) — Agora is
an SF Bay aggregator. Each event's location reliably names its city; titles
don't, so we filter on location.
"""
from scrapers import bay_area, luma
from scrapers.base import RawEvent


SOURCE = "luma.com"
NAME = "Reading Rhythms"
CALENDAR_URL = "https://luma.com/readingrhythms-ca"

# Back-compat alias for tests that reference the module-local name.
_is_bay_area = bay_area.is_bay_area


def matches(url: str) -> bool:
    return "luma.com/readingrhythms-ca" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return [e for e in luma.scrape_calendar(url) if bay_area.is_bay_area(e.location)]
