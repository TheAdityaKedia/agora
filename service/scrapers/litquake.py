"""Litquake events scraper (via the festival's Sched iCal feed).

Litquake (SF's big annual literary festival) publishes its schedule on Sched,
which exposes a full iCal feed. Thin wrapper around scrapers/ics.py.

NOTE: Sched uses a per-year subdomain (litquake2026, litquake2027, …), so the
feed URL must be bumped each year when the new festival's schedule goes up.
Off-season the feed may be empty or 404 until the next program is published.
"""
from scrapers import ics
from scrapers.base import RawEvent


SOURCE = "litquake.org"
NAME = "Litquake"
ICS_URL = "https://litquake2026.sched.com/all.ics"
AREA = "San Francisco Bay Area"


def matches(url: str) -> bool:
    return "litquake" in url


def scrape(url: str = ICS_URL) -> list[RawEvent]:
    return ics.scrape_ics(ICS_URL, fallback_location=AREA)
