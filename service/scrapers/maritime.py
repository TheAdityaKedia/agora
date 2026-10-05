"""San Francisco Maritime National Park Association events scraper.

The nonprofit behind the USS Pampanito and the Hyde Street Pier programs:
chantey sings, Fleet Week viewing, the Maritime Ball. Squarespace Events
Collection → thin wrapper around scrapers/squarespace_events.py; cards carry
no excerpt, so descriptions come from the detail pages.

Its events are spread along the waterfront, and most cards carry no address,
so there is no one fallback venue (an earlier "Pier 45" fallback misplaced
the chantey sings and the Ball). Locations are fixed per event: a card that
names Pier 45 gets a mappable Pier 45 address, the monthly chantey sing gets
the Maritime Museum (its description says so), and anything else is placed
only in San Francisco rather than at a guessed venue.
"""
import re

from scrapers import squarespace_events
from scrapers.base import RawEvent


SOURCE = "maritime.org"
NAME = 'San Francisco Maritime National Park Association'
CALENDAR_URL = "https://maritime.org/events"
ADDRESS = "San Francisco, CA"
PIER_45 = "Pier 45, San Francisco, CA 94133"
MARITIME_MUSEUM = "Maritime Museum, 900 Beach St, San Francisco, CA 94109"

_PIER_45_RE = re.compile(r"\bpier 45\b", re.I)
_CHANTEY_RE = re.compile(r"\bchantey sing\b", re.I)


def matches(url: str) -> bool:
    return "maritime.org" in url


def place(ev: RawEvent) -> RawEvent:
    """Give `ev` a location the venue resolver can map. Pure."""
    if _PIER_45_RE.search(ev.location or ""):
        ev.location = PIER_45
    elif _CHANTEY_RE.search(ev.title):
        ev.location = MARITIME_MUSEUM
    elif not ev.location:
        ev.location = ADDRESS
    return ev


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    events = squarespace_events.scrape_collection(
        CALENDAR_URL, enrich_descriptions=True, card_address=True)
    return [place(e) for e in events]
