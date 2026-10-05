"""Fort Mason Center for Arts & Culture events scraper.

The Marina waterfront campus (galleries, Cowell Theater, festivals, the
farmers market). WordPress + The Events Calendar → scrapers/tribe_events.py.
Like Oakland Art Murmur, the API repeats each exhibition once per day it's on
view (700+ entries for one show), so repeated titles collapse to the earliest
upcoming occurrence (oaklandartmurmur.collapse_by_title).
"""
from scrapers import oaklandartmurmur, tribe_events
from scrapers.base import RawEvent


SOURCE = "fortmason.org"
NAME = 'Fort Mason Center'
SITE_BASE = "https://fortmason.org"
EVENTS_URL = "https://fortmason.org/events/"
ADDRESS = 'Fort Mason Center for Arts & Culture, 2 Marina Blvd, San Francisco, CA 94123'


def matches(url: str) -> bool:
    return "fortmason.org" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return oaklandartmurmur.collapse_by_title(tribe_events.scrape_events(SITE_BASE, fallback_location=ADDRESS))
