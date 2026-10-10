"""Fort Mason Center for Arts & Culture events scraper.

The Marina waterfront campus (galleries, Cowell Theater, festivals, the
farmers market). WordPress + The Events Calendar → scrapers/tribe_events.py.
Like Oakland Art Murmur, the API repeats each exhibition once per day it's on
view (700+ entries for one show), so repeated titles collapse to the earliest
upcoming occurrence (oaklandartmurmur.collapse_by_title).

Descriptions arrive with the page builder's tab bar flattened in front
("@ About Event Details About The Artists Gallery Plan Your Visit Reserve
Space <the real text>"); strip_tabs cuts it.
"""
import re

from scrapers import oaklandartmurmur, tribe_events
from scrapers.base import RawEvent


SOURCE = "fortmason.org"
NAME = 'Fort Mason Center'
SITE_BASE = "https://fortmason.org"
EVENTS_URL = "https://fortmason.org/events/"
ADDRESS = 'Fort Mason Center for Arts & Culture, 2 Marina Blvd, San Francisco, CA 94123'


# The tab bar always ends with "Plan Your Visit", sometimes followed by a few
# more short tab labels.
_TABS_RE = re.compile(r"^@.{0,200}?\bPlan Your Visit\b\s*((Reserve Space|Tickets|Related Events)\s*)*", re.I | re.S)


def matches(url: str) -> bool:
    return "fortmason.org" in url


def strip_tabs(text: str | None) -> str | None:
    """Drop the flattened tab bar at the start of a description. Pure."""
    if not text:
        return text
    return _TABS_RE.sub("", text, count=1).strip() or None


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    events = oaklandartmurmur.collapse_by_title(tribe_events.scrape_events(SITE_BASE, fallback_location=ADDRESS))
    for e in events:
        e.description = strip_tabs(e.description)
    return events
