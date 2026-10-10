"""EastSide Arts Alliance events scraper.

East Oakland cultural center (International Blvd) presenting film nights,
book clubs, youth arts programs, Día de los Muertos and community rituals.
Squarespace events collection read through its ``?format=json`` feed
(scrapers/squarespace_events.py::scrape_json), which carries each event's
address (the center and its 2285 International Blvd annex) and image. The
feed's excerpts are just the date, so descriptions come from each event's
page (``enrich_descriptions``), minus the stray "HERE" of its link texts.
"""
import re

from scrapers import squarespace_events
from scrapers.base import RawEvent


SOURCE = "eastsideartsalliance.org"
NAME = "EastSide Arts Alliance"
CALENDAR_URL = "https://www.eastsideartsalliance.org/calendar"
ADDRESS = "EastSide Arts Alliance, 2277 International Blvd, Oakland, CA 94606"

_HERE_RE = re.compile(r"\s*\bHERE\b\s*")


def matches(url: str) -> bool:
    return "eastsideartsalliance.org" in url


def clean_description(text: str | None) -> str | None:
    """Drop the bare "HERE" left by "register HERE" links. Pure."""
    if not text:
        return text
    return re.sub(r"\s{2,}", " ", _HERE_RE.sub(" ", text)).strip() or None


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    events = squarespace_events.scrape_json(CALENDAR_URL, fallback_location=ADDRESS, enrich_descriptions=True)
    for e in events:
        e.description = clean_description(e.description)
    return events
