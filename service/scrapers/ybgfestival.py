"""Yerba Buena Gardens Festival events scraper.

Free outdoor performances (music, dance, family) in Yerba Buena Gardens,
May–Oct. Distinct from YBCA. WordPress + The Events Calendar → thin wrapper
around scrapers/tribe_events.py (venue blocks name the garden/lawn).

Some venue blocks give only cross streets ("Crepe Myrtle Garden, Yerba Buena
Gardens, 3rd St. between Mission and Howard Sts."), which the map can't
place, so a location without a house number is rewritten to the garden area
plus the Gardens' street address.
"""
import re

from scrapers import tribe_events
from scrapers.base import RawEvent


SOURCE = "ybgfestival.org"
NAME = 'Yerba Buena Gardens Festival'
SITE_BASE = "https://ybgfestival.org"
EVENTS_URL = "https://ybgfestival.org/"
STREET = "Yerba Buena Gardens, 750 Howard St, San Francisco, CA 94103"
ADDRESS = STREET

# A house number followed by a street name: "799 Howard St."
_HOUSE_NUMBER_RE = re.compile(r"(?<![\w-])\d{1,5}\s+[a-z]", re.I)


def matches(url: str) -> bool:
    return "ybgfestival.org" in url


def place(ev: RawEvent) -> RawEvent:
    """Make a cross-streets-only location mappable. Pure."""
    loc = ev.location or ""
    if not _HOUSE_NUMBER_RE.search(loc):
        area = loc.split(",")[0].strip()
        ev.location = STREET if not area or "yerba buena gardens" in area.lower() else f"{area}, {STREET}"
    return ev


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return [place(e) for e in tribe_events.scrape_events(SITE_BASE, fallback_location=ADDRESS)]
