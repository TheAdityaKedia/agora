"""The Riptide events scraper (via its Elfsight Event Calendar widget).

The Riptide (a classic Ocean Beach / Outer Sunset neighborhood bar with nightly
live music, DJs, karaoke, trivia, and bingo) is a React SPA whose calendar is an
Elfsight Event Calendar widget. Thin wrapper around scrapers/elfsight_events.py;
only the widget source id is venue-specific.
"""
from scrapers import elfsight_events
from scrapers.base import RawEvent


SOURCE = "riptidesf.com"
NAME = "The Riptide"
SOURCE_ID = "29d1ee29-907a-4638-b72f-b2292e3a1f73"
SITE_URL = "https://www.riptidesf.com/"
ADDRESS = "The Riptide, 3639 Taraval St, San Francisco, CA 94116"


def matches(url: str) -> bool:
    return "riptidesf.com" in url


def scrape(url: str = SITE_URL) -> list[RawEvent]:
    return elfsight_events.scrape_events(
        SOURCE_ID, fallback_location=ADDRESS, fallback_url=SITE_URL,
    )
