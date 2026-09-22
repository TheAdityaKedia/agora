"""Tests for scrapers/luma.py — the shared library that per-source Luma
wrappers (like bigbrainbay.py) plug into.
"""
from datetime import datetime, timezone

from scrapers.base import RawEvent
from scrapers import bigbrainbay, luma


# Minimal calendar page: ItemList JSON-LD with two Event items + one non-Event
# Organization block that must be ignored.
CALENDAR_HTML = """
<html><head>
  <script type="application/ld+json">
    {"@type":"Organization","name":"Big Brain Lectures - Bay Area",
     "url":"https://luma.com/Big-Brain-Bay"}
  </script>
  <script type="application/ld+json">
    {
      "@context":"https://schema.org",
      "@type":"ItemList",
      "name":"Upcoming Events on Big Brain Lectures - Bay Area",
      "url":"https://luma.com/Big-Brain-Bay",
      "numberOfItems":2,
      "itemListElement":[
        {"@type":"ListItem","position":1,"item":{
          "@type":"Event",
          "@id":"https://luma.com/6z48og5t",
          "url":"https://luma.com/6z48og5t",
          "name":"The Search for Answers Behind Rising Cancer Rates",
          "location":{"@type":"Place","name":"Donkey & Goat Winery",
            "address":{"@type":"PostalAddress","streetAddress":"Donkey & Goat Winery",
              "addressLocality":"Berkeley","addressRegion":"California"}},
          "image":["https://images.lumacdn.com/uploads/wo/x.png"],
          "startDate":"2026-09-22T19:00:00.000-07:00",
          "endDate":"2026-09-22T21:00:00.000-07:00"
        }},
        {"@type":"ListItem","position":2,"item":{
          "@type":"Event",
          "@id":"https://luma.com/f132lcgs",
          "url":"https://luma.com/f132lcgs",
          "name":"The \\"Mythological\\" Genre: How Hindu Stories Shaped Global Pop Culture",
          "location":{"@type":"Place","name":"473A Haight St",
            "address":{"@type":"PostalAddress","streetAddress":"473A Haight St",
              "addressLocality":"San Francisco","addressRegion":"California"}},
          "image":"https://images.lumacdn.com/uploads/iu/y.png",
          "startDate":"2026-09-23T19:00:00.000-07:00"
        }}
      ]
    }
  </script>
</head><body></body></html>
"""

EVENT_HTML = """
<html><head>
  <meta property="og:description" content="Short truncated preview.">
  <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Event",
     "name":"The Search for Answers Behind Rising Cancer Rates",
     "url":"https://luma.com/6z48og5t",
     "description":"Full detail-page blurb explaining the lecture in detail.",
     "startDate":"2026-09-22T19:00:00.000-07:00"}
  </script>
</head><body></body></html>
"""


# --- matches --------------------------------------------------------------

def test_bigbrainbay_matches_only_its_calendar():
    assert bigbrainbay.matches("https://luma.com/Big-Brain-Bay")
    assert bigbrainbay.matches("https://luma.com/Big-Brain-Bay/")
    assert not bigbrainbay.matches("https://luma.com/some-other-calendar")
    assert not bigbrainbay.matches("https://www.eventbrite.com/o/x")


# --- find_calendar_events -------------------------------------------------

def test_find_calendar_events_extracts_events_from_itemlist():
    events = luma.find_calendar_events(CALENDAR_HTML)
    assert len(events) == 2
    assert events[0]["name"].startswith("The Search")
    assert events[1]["name"].startswith("The ")  # "\"Mythological\"" with escapes


def test_find_calendar_events_ignores_non_itemlist_blocks():
    """The page's Organization JSON-LD block must not confuse the extractor."""
    events = luma.find_calendar_events(CALENDAR_HTML)
    for ev in events:
        assert ev.get("@type") == "Event"


def test_find_calendar_events_returns_empty_when_no_itemlist():
    assert luma.find_calendar_events("<html><body>no jsonld here</body></html>") == []


# --- event_from_json_ld ---------------------------------------------------

def test_event_from_json_ld_yields_full_rawevent():
    events = luma.find_calendar_events(CALENDAR_HTML)
    ev = luma.event_from_json_ld(events[0])
    assert isinstance(ev, RawEvent)
    assert ev.title.startswith("The Search")
    # 7pm PT on Sep 22 = 02:00 UTC Sep 23 (PDT, UTC-7)
    assert ev.start_time == datetime(2026, 9, 23, 2, 0, tzinfo=timezone.utc)
    assert "Berkeley" in ev.location
    assert "Donkey & Goat Winery" in ev.location
    assert ev.url == "https://luma.com/6z48og5t"
    assert ev.image_url == "https://images.lumacdn.com/uploads/wo/x.png"
    # Calendar-level payload has no description; enriched via detail fetch.
    assert ev.description is None


def test_event_from_json_ld_returns_none_when_missing_fields():
    assert luma.event_from_json_ld({"@type": "Event"}) is None
    assert luma.event_from_json_ld({"@type": "Event", "name": "x"}) is None
    assert luma.event_from_json_ld(
        {"@type": "Event", "startDate": "2026-09-22T19:00:00-07:00"}
    ) is None


def test_format_location_dedupes_repeated_name_and_street():
    """Luma sometimes duplicates the venue name into `streetAddress`. Don't
    repeat it in the composed location string.
    """
    loc = {
        "name": "Donkey & Goat Winery",
        "address": {"streetAddress": "Donkey & Goat Winery",
                    "addressLocality": "Berkeley", "addressRegion": "California"},
    }
    s = luma._format_location(loc)
    assert s.count("Donkey & Goat Winery") == 1
    assert "Berkeley" in s


# --- parse_event_description ---------------------------------------------

def test_parse_event_description_prefers_json_ld_over_og():
    """JSON-LD Event.description is the full unabridged text; og:description
    is a ~300-char preview. Prefer the JSON-LD one.
    """
    desc = luma.parse_event_description(EVENT_HTML)
    assert desc == "Full detail-page blurb explaining the lecture in detail."


def test_parse_event_description_falls_back_to_og_description():
    """When JSON-LD Event has no description, og:description is used."""
    html = """
    <html><head>
      <meta property="og:description" content="Only meta here.">
      <script type="application/ld+json">
        {"@type":"Event","name":"x","startDate":"2026-09-22T19:00:00-07:00"}
      </script>
    </head></html>
    """
    assert luma.parse_event_description(html) == "Only meta here."


def test_parse_event_description_none_when_no_source():
    assert luma.parse_event_description("<html><body></body></html>") is None


# --- scrape_calendar (end-to-end with stubbed fetchers) ------------------

def test_scrape_calendar_end_to_end_enriches_descriptions():
    seen_events: list[str] = []

    def _cal(url: str) -> str:
        assert url == "https://luma.com/Big-Brain-Bay"
        return CALENDAR_HTML

    def _event(url: str) -> str | None:
        seen_events.append(url)
        # Same detail HTML for every event — the first event's real URL is
        # /6z48og5t, so its description will match EVENT_HTML.
        return EVENT_HTML

    events = luma.scrape_calendar(
        "https://luma.com/Big-Brain-Bay",
        calendar_html_fetch=_cal,
        event_html_fetch=_event,
    )
    assert len(events) == 2
    # Both events had their descriptions enriched from the stub detail fetch.
    assert all(e.description for e in events)
    assert events[0].description.startswith("Full detail-page blurb")
    # Detail fetches happened once per unique URL.
    assert sorted(seen_events) == sorted(["https://luma.com/6z48og5t",
                                          "https://luma.com/f132lcgs"])


def test_scrape_calendar_returns_empty_on_calendar_fetch_error():
    import requests

    def _cal_error(url: str) -> str:
        raise requests.ConnectionError("simulated")

    events = luma.scrape_calendar(
        "https://luma.com/Big-Brain-Bay",
        calendar_html_fetch=_cal_error,
        event_html_fetch=lambda u: None,
    )
    assert events == []
