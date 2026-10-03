"""The Henry J. Kaiser Center for the Arts events scraper (via OvationTix).

The Henry J (Oakland's former Kaiser Convention Center by Lake Merritt; the
Calvin Simmons Theater plus ballrooms) books concerts, comedy, family shows
and touring exhibitions. Ticketing is OvationTix client 36995, so this is a
thin wrapper around scrapers/ovationtix.py, which also collapses the touring
exhibitions' timed-entry slots to one listing.
"""
from scrapers import ovationtix
from scrapers.base import RawEvent


SOURCE = "thehenryj.org"
NAME = "The Henry J. Kaiser Center for the Arts"
CLIENT_ID = "36995"
EVENTS_URL = "https://www.thehenryj.org/upcoming-events"
ADDRESS = "Henry J. Kaiser Center for the Arts, 10 10th St, Oakland, CA 94607"


def matches(url: str) -> bool:
    return "thehenryj.org" in url or "hjkarts.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return ovationtix.scrape_client(CLIENT_ID, fallback_location=ADDRESS, tag="henryj")
