"""Balboa Theatre events scraper (via Veezi).

The Balboa (a CinemaSF neighborhood cinema in the Richmond) embeds a Veezi
sessions widget. This is a thin wrapper around scrapers/veezi.py — the shared
library fetches the sessions page, reads its JSON-LD showtimes, and maps poster
images. Only the venue's Veezi site token is venue-specific.
"""
from scrapers import veezi
from scrapers.base import RawEvent


SOURCE = "balboamovies.com"
NAME = "Balboa Theatre"
SITE_TOKEN = "52wkfzmjpwjjfpz3ye7tz8wscg"
CALENDAR_URL = "https://www.balboamovies.com/calendar-of-events"
ADDRESS = "Balboa Theatre, 3630 Balboa St, San Francisco, CA 94121"


def matches(url: str) -> bool:
    return "balboamovies.com" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return veezi.scrape_sessions(SITE_TOKEN, fallback_location=ADDRESS)
