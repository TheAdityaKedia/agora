"""Reading Rhythms California events — via Luma.

A thin wrapper around scrapers/luma.py (see bigbrainbay.py for the pattern).
Reading Rhythms is a "reading party" series — read with friends to live music
and curated playlists. The California calendar mixes Bay Area and LA-area
events; every event's title is prefixed with its city ("Reading Rhythms San
Francisco: …", "Reading Rhythms LA: …").
"""
from scrapers import luma
from scrapers.base import RawEvent


SOURCE = "luma.com"
NAME = "Reading Rhythms"
CALENDAR_URL = "https://luma.com/readingrhythms-ca"


def matches(url: str) -> bool:
    return "luma.com/readingrhythms-ca" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return luma.scrape_calendar(url)
