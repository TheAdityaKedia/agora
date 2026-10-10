"""The Booksmith events scraper.

The Booksmith (Haight-Ashbury) hosts author talks, launches, and its drag/literary series. Runs on IndieCommerce; thin wrapper around scrapers/indiecommerce.py.
Only the site base and address are venue-specific.
"""
from scrapers import indiecommerce
from scrapers.base import RawEvent
from scrapers.browser import browser_context, resilient_fetcher


SOURCE = "booksmith.com"
NAME = "The Booksmith"
SITE_BASE = "https://booksmith.com"
EVENTS_URL = "https://booksmith.com/events"
ADDRESS = "The Booksmith, 1727 Haight St, San Francisco, CA 94117"


def matches(url: str) -> bool:
    return "booksmith.com" in url


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    # Since Oct 2026 the whole site sits behind Cloudflare's managed challenge
    # (403 "Just a moment..." to plain requests), so fetch with full Chromium,
    # the Green Apple route (CONTRIBUTING → "When to give up").
    with browser_context(full_chromium=True) as context:
        fetch = resilient_fetcher(context, lambda m: print(f"[booksmith] {m}", flush=True))
        return indiecommerce.scrape_events(SITE_BASE, fallback_location=ADDRESS, tag="booksmith",
                                           get=fetch, workers=1)
