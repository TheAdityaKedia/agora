"""Tests for scrapers/eventbrite.py — the shared library that per-source
Eventbrite wrappers (like phoenix.py) plug into.
"""
from datetime import datetime, timezone

import pytest

from scrapers.base import RawEvent
from scrapers import eventbrite, phoenix

# Minimal organizer-page HTML: two event tiles, plus some noise anchors.
ORGANIZER_HTML = """
<html><body>
  <nav><a href="/">Home</a></nav>
  <a href="/e/skitzo-45-years-of-gut-wrenching-thrash-metal-tickets-1999053095622?aff=xyz">
    Skitzo
  </a>
  <a href="/e/the-rocky-horror-picture-show-friday-10pm-show-tickets-1996318373996">
    Rocky Horror
  </a>
  <a href="/e/the-rocky-horror-picture-show-friday-10pm-show-tickets-1996318373996?utm=dup">
    Rocky Horror duplicate
  </a>
  <a href="https://www.eventbrite.com/d/us/all-events">All Events (nav)</a>
</body></html>
"""

EVENT_HTML = """
<html><head>
  <script type="application/ld+json">
    { "@type": "WebPage", "name": "wrapper" }
  </script>
  <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Event",
      "name": "Skitzo: 45 Years of Gut-Wrenching Thrash Metal",
      "description": "Thrash from the archives",
      "url": "https://www.eventbrite.com/e/skitzo-45-years-of-gut-wrenching-thrash-metal-tickets-1999053095622",
      "image": "https://img.evbuc.com/skitzo.jpg",
      "startDate": "2026-11-14T20:00:00-08:00",
      "endDate": "2026-11-15T00:00:00-08:00",
      "location": {
        "@type": "Place",
        "name": "The Phoenix Theater",
        "address": {
          "@type": "PostalAddress",
          "streetAddress": "201 Washington St., Petaluma, CA",
          "addressLocality": "Petaluma",
          "addressRegion": "CA"
        }
      }
    }
  </script>
</head><body></body></html>
"""


def test_find_organizer_event_urls_dedupes_and_absolutizes():
    urls = eventbrite.find_organizer_event_urls(ORGANIZER_HTML)
    assert urls == [
        "https://www.eventbrite.com/e/skitzo-45-years-of-gut-wrenching-thrash-metal-tickets-1999053095622",
        "https://www.eventbrite.com/e/the-rocky-horror-picture-show-friday-10pm-show-tickets-1996318373996",
    ]


def test_parse_event_page_finds_event_type_only():
    obj = eventbrite.parse_event_page(EVENT_HTML)
    assert obj is not None
    assert obj.get("@type") == "Event"
    assert obj["name"].startswith("Skitzo")


def test_event_from_json_ld_yields_full_rawevent():
    obj = eventbrite.parse_event_page(EVENT_HTML)
    ev = eventbrite.event_from_json_ld(obj)
    assert isinstance(ev, RawEvent)
    assert ev.title.startswith("Skitzo")
    # 8pm PT on Nov 14 = 04:00 UTC Nov 15
    assert ev.start_time == datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)
    assert "Petaluma" in ev.location
    assert "201 Washington" in ev.location
    assert ev.image_url == "https://img.evbuc.com/skitzo.jpg"
    assert ev.url.startswith("https://www.eventbrite.com/e/skitzo-")


def test_event_from_json_ld_location_override():
    obj = eventbrite.parse_event_page(EVENT_HTML)
    ev = eventbrite.event_from_json_ld(obj, location_override="Custom Venue Address")
    assert ev.location == "Custom Venue Address"


def test_scrape_organizer_with_stubbed_fetchers():
    """End-to-end using stubs — organizer HTML gives 2 unique urls, each stub
    returns the same event JSON-LD, so we get 2 RawEvents."""
    def stub_org(url):
        return ORGANIZER_HTML

    def stub_event(url):
        return EVENT_HTML

    events = eventbrite.scrape_organizer(
        "https://www.eventbrite.com/o/phoenix-theater-26319831111",
        organizer_html_fetch=stub_org,
        event_html_fetch=stub_event,
    )
    assert len(events) == 2
    for ev in events:
        assert isinstance(ev, RawEvent)
        assert ev.start_time.tzinfo == timezone.utc


def test_phoenix_matches_organizer_and_website():
    assert phoenix.matches("https://www.eventbrite.com/o/phoenix-theater-26319831111")
    assert phoenix.matches("https://phoenixtheater.com/")
    assert not phoenix.matches("https://phoenixtheatresf.org/")
