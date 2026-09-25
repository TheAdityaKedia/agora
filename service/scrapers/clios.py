"""Clio's Books events scraper (Eventbrite links from its own homepage).

Clio's Books (an Oakland Grand Lake bookstore focused on history and ideas)
lists its events on a Squarespace homepage as "TICKETS" buttons pointing at
Eventbrite. Its Eventbrite organizer page only renders a couple of events
(the rest sit behind a "show more" XHR that 403s), so the homepage is the
fuller index: collect its event ids, then let scrapers/eventbrite.py fetch
each event's JSON-LD. Links come in several shapes (slug-tickets-<id>,
bare <id>, -registration-<id>), so we normalize to the id-only URL, which
Eventbrite redirects to the canonical page.
"""
import re

import requests

from scrapers import eventbrite
from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "cliosbooks.com"
NAME = "Clio's Books"
HOME_URL = "https://www.cliosbooks.com/"
REQUEST_TIMEOUT = 25

_EVENT_ID_RE = re.compile(r"eventbrite\.com/e/(?:[\w-]*-)?(\d{9,})")


def matches(url: str) -> bool:
    return "cliosbooks.com" in url


def find_event_urls(html: str) -> list[str]:
    """Eventbrite event ids linked from the page, as id-only URLs. Pure."""
    urls: list[str] = []
    for event_id in _EVENT_ID_RE.findall(html):
        url = f"https://www.eventbrite.com/e/{event_id}"
        if url not in urls:
            urls.append(url)
    return urls


def _fetch_home() -> str:
    resp = requests.get(HOME_URL, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def scrape(url: str = HOME_URL) -> list[RawEvent]:
    urls = find_event_urls(_fetch_home())
    print(f"[clios] {len(urls)} Eventbrite events linked from homepage", flush=True)
    return eventbrite.scrape_event_urls(urls)
