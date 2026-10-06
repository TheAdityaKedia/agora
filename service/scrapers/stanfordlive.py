"""Stanford Live events scraper (via Spektrix).

Stanford University's performing-arts presenter (Bing Concert Hall, Frost
Amphitheater, Dinkelspiel, Memorial Auditorium) plus the Department of Music's
concerts, all ticketed through Spektrix. Thin wrapper around
scrapers/spektrix.py; one event per performance. The API names no hall, so
events fall back to the campus. Kronos Quartet's Stanford dates also appear
here; cross-source dedup merges them.

Dropped: "Student Lottery Winners" allocations and "Student Matinee" school
shows (not public; they also carry no web page). Other events without a page
link to the calendar.
"""
import re

from scrapers import spektrix
from scrapers.base import RawEvent


SOURCE = "live.stanford.edu"
NAME = "Stanford Live"
API_BASE = "https://ticketing.purchase.live.stanford.edu/stanfordlive/api/v3"
EVENTS_URL = "https://live.stanford.edu/calendar"
ADDRESS = "Stanford Live, Stanford University, Stanford, CA 94305"

_NOT_PUBLIC_RE = re.compile(r"\bstudent (lottery|matinee)\b", re.I)


def matches(url: str) -> bool:
    return "live.stanford.edu" in url


def public(events: list[RawEvent]) -> list[RawEvent]:
    """Drop non-public allocations; link page-less events to the calendar. Pure."""
    out = []
    for e in events:
        if _NOT_PUBLIC_RE.search(e.title):
            continue
        e.url = e.url or EVENTS_URL
        out.append(e)
    return out


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return public(spektrix.scrape(API_BASE, fallback_location=ADDRESS, tag="stanfordlive"))
