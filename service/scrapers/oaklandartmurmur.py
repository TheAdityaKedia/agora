"""Oakland Art Murmur events scraper (via The Events Calendar / Tribe API).

Oakland Art Murmur (a coalition of Oakland galleries; First Friday art walks and
gallery openings) runs WordPress with The Events Calendar plugin, so it reuses
scrapers/tribe_events.py. One wrinkle: the API lists ongoing gallery
exhibitions as a separate entry for every day they're on view (one show can
appear 150+ times). We collapse repeated titles to their earliest upcoming
occurrence so each exhibition/reception is a single event.
"""
from scrapers import tribe_events
from scrapers.base import RawEvent


SOURCE = "oaklandartmurmur.org"
NAME = "Oakland Art Murmur"
SITE_BASE = "https://oaklandartmurmur.org"
AREA = "Oakland, CA"


def matches(url: str) -> bool:
    return "oaklandartmurmur.org" in url


def collapse_by_title(events: list[RawEvent]) -> list[RawEvent]:
    """Keep one event per title — the earliest — so multi-day exhibition runs
    don't flood the manifest with a near-identical entry per day."""
    best: dict[str, RawEvent] = {}
    for e in events:
        cur = best.get(e.title)
        if cur is None or e.start_time < cur.start_time:
            best[e.title] = e
    return sorted(best.values(), key=lambda e: e.start_time)


def scrape(url: str = SITE_BASE) -> list[RawEvent]:
    return collapse_by_title(tribe_events.scrape_events(SITE_BASE, fallback_location=AREA))
