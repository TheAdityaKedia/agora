"""Frontier Tower (SF) events — via Luma.

A thin wrapper around scrapers/luma.py (see bigbrainbay.py for the pattern).
Frontier Tower is a 16-floor member-run tech/science hub at 995 Market St; its
Luma calendar lists talks, hackathons, reading groups, and member socials.

The calendar also carries internal bookkeeping entries — "HOLD - 2nd Floor
Private Rental (...)" room blocks, "test" events, and "TBA ... Placeholder"
slots — which aren't public events, so they're dropped here.
"""
import re

from scrapers import luma
from scrapers.base import RawEvent


SOURCE = "luma.com"
NAME = "Frontier Tower"
CALENDAR_URL = "https://luma.com/frontiertower"

_NOISE_RE = re.compile(r"^\s*(hold\b|test\b)|\bplaceholder\b", re.IGNORECASE)


def matches(url: str) -> bool:
    return "luma.com/frontiertower" in url


def is_noise(title: str) -> bool:
    return bool(_NOISE_RE.search(title))


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return [e for e in luma.scrape_calendar(url) if not is_noise(e.title)]
