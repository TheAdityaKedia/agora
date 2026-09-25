"""Books Inc. events scraper (via its Elfsight Event Calendar widget).

Books Inc. (the Bay Area's oldest independent bookstore chain: Marina, Laurel
Village, Alameda, Berkeley, Palo Alto, Mountain View, Campbell, …) runs its
events page on Shopify with an Elfsight Event Calendar whose events are
embedded in the widget settings, not the events API. Thin wrapper around
scrapers/elfsight_events.py::scrape_widget_settings; each event resolves its
own store (or off-site venue) from the widget's location list.
"""
from scrapers import elfsight_events
from scrapers.base import RawEvent


SOURCE = "booksinc.com"
NAME = "Books Inc."
WIDGET_ID = "85b32f03-8a60-406b-8fe1-223ad02c821d"
EVENTS_URL = "https://www.booksinc.com/pages/events"


def matches(url: str) -> bool:
    return "booksinc.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    return elfsight_events.scrape_widget_settings(WIDGET_ID, EVENTS_URL, fallback_location="Books Inc.")
