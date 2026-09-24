"""Reading Rhythms California events — via Luma.

A thin wrapper around scrapers/luma.py (see bigbrainbay.py for the pattern).
Reading Rhythms is a "reading party" series — read with friends to live music
and curated playlists. The California calendar is statewide (San Francisco, LA,
San Diego, …), so we keep only Bay Area events — Agora is an SF Bay aggregator.
Each event's location reliably names its city; titles don't, so we filter on
location.
"""
import re

from scrapers import luma
from scrapers.base import RawEvent


SOURCE = "luma.com"
NAME = "Reading Rhythms"
CALENDAR_URL = "https://luma.com/readingrhythms-ca"

# Bay Area cities/regions we keep (matched case-insensitively in the location).
_BAY_AREA_RE = re.compile(
    r"\b(san francisco|sf|oakland|berkeley|san jose|bay area|alameda|emeryville"
    r"|richmond|marin|sausalito|daly city|south san francisco|fremont|hayward"
    r"|palo alto|mountain view|sunnyvale|santa clara|cupertino|redwood city"
    r"|menlo park|san mateo|burlingame|los gatos|milpitas|union city|san rafael"
    r"|novato|petaluma|santa rosa|sonoma|napa|vallejo|walnut creek|concord"
    r"|pleasanton|livermore|dublin|el cerrito|albany|san leandro|pacifica)\b",
    re.IGNORECASE,
)


def matches(url: str) -> bool:
    return "luma.com/readingrhythms-ca" in url


def _is_bay_area(location: str | None) -> bool:
    return bool(location and _BAY_AREA_RE.search(location))


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return [e for e in luma.scrape_calendar(url) if _is_bay_area(e.location)]
