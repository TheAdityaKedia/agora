"""GLBT Historical Society events scraper.

The Castro's queer history museum + archive: talks, film nights, walking
tours, workshops. Squarespace Events Collection → thin wrapper around
scrapers/squarespace_events.py. Some events are off-site (e.g. the annual
Reunion at a hotel), so each card's own address line is used. Descriptions
open with logistics paragraphs ("LOCATION …", "ADMISSION …", "RSVP and
reserve tickets here"); those are dropped so the text starts with the blurb.
"""
import re

from scrapers import squarespace_events
from scrapers.base import RawEvent


SOURCE = "glbthistory.org"
NAME = 'GLBT Historical Society'
CALENDAR_URL = "https://www.glbthistory.org/events"
ADDRESS = 'GLBT Historical Society Museum, 4127 18th St, San Francisco, CA 94114'


LOGISTICS_RE = re.compile(r"^(LOCATION|ADMISSION)\b|^RSVP and reserve tickets here\.?$", re.I)


def matches(url: str) -> bool:
    return "glbthistory.org" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return squarespace_events.scrape_collection(
        CALENDAR_URL, fallback_location=ADDRESS, card_address=True, upcoming_only=True,
        skip_paragraphs=LOGISTICS_RE)
