import json
import re
from datetime import datetime, timezone
from pathlib import Path

from scrapers import partiful

FIXTURES = Path(__file__).parent / "fixtures"
EXPLORE_HTML = (FIXTURES / "partiful_explore.html").read_text()
EVENT_HTML = (FIXTURES / "partiful_event.html").read_text()
# Fixture events are in Oct–Dec 2026; pin "now" before them.
NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def _explore_events():
    return partiful.parse_explore(EXPLORE_HTML)


def _by_id(events, event_id):
    return next(e for e in events if e["id"] == event_id)


def test_matches_explore_url_only():
    assert partiful.matches("https://partiful.com/explore/sf")
    assert not partiful.matches("https://partiful.com/e/79v5MvdmXJreSkcvfI4n")
    assert not partiful.matches("https://luma.com/thecommons")


def test_parse_explore_collects_distinct_events_across_sections_and_feed():
    events = _explore_events()
    ids = [e["id"] for e in events]
    # trending (1) + two sections (4, one repeated) + feed (2) → 7 distinct
    assert len(ids) == 7 == len(set(ids))
    assert ids[0] == "oNEMhoRV3hnHziZG5zHh"  # trending first, in page order


def test_to_raw_event_maps_structured_event():
    raw = partiful.to_raw_event(_by_id(_explore_events(), "niZdDIpgOIp7FKdOSg9j"), now=NOW)
    assert raw.title == "costco hotdog run 🌭"
    assert raw.start_time == datetime(2026, 10, 3, 17, 30, tzinfo=timezone.utc)
    assert raw.location == "フェリービルディング, 1 Ferry Building, San Francisco, CA 94111"
    assert raw.url == "https://partiful.com/e/niZdDIpgOIp7FKdOSg9j"
    assert raw.image_url.startswith("https://partiful.imgix.net/external/user/H9gFZ8UUp5hV9VSocBi8ki2a11r2/")
    assert raw.description


def test_to_raw_event_omits_place_name_already_in_address():
    event, _, _ = partiful.parse_event_page(EVENT_HTML)
    raw = partiful.to_raw_event(event, now=NOW)
    assert raw.location == "San Francisco, CA"


def test_to_raw_event_keeps_freeform_location_event_without_a_location():
    raw = partiful.to_raw_event(_by_id(_explore_events(), "4vECncctTYvP1ki68Pa2"), now=NOW)
    assert raw is not None and raw.location is None


def test_to_raw_event_drops_non_bay_area_address():
    event = json.loads(json.dumps(_by_id(_explore_events(), "xR2ROPI4OszDptT17Rjb")))
    event["locationInfo"]["mapsInfo"]["addressLines"] = ["123 Sunset Blvd", "Los Angeles, CA 90028"]
    assert partiful.to_raw_event(event, now=NOW) is None


def test_to_raw_event_public_only_unless_told_otherwise():
    event = dict(_by_id(_explore_events(), "xR2ROPI4OszDptT17Rjb"), isPublic=False)
    assert partiful.to_raw_event(event, now=NOW) is None
    # A link someone sent us is consent to index it, even if not public.
    assert partiful.to_raw_event(event, now=NOW, require_public=False) is not None


def test_to_raw_event_drops_past_and_unpublished():
    event = _by_id(_explore_events(), "xR2ROPI4OszDptT17Rjb")
    assert partiful.to_raw_event(event, now=datetime(2027, 1, 1, tzinfo=timezone.utc)) is None
    assert partiful.to_raw_event(dict(event, status="DRAFT"), now=NOW) is None


def test_parse_event_page_returns_event_similar_ids_and_region():
    event, similar, region = partiful.parse_event_page(EVENT_HTML)
    assert event["id"] == "79v5MvdmXJreSkcvfI4n"
    assert region == "SF"
    assert [s["id"] for s in similar][:2] == ["uoxo5Qw2sRG32y0KNCow", "eFrXxYIuhEFIKe7GAAD1"]


def _event_page(event_id, similar_ids, region="SF", start="2026-10-10T18:00:00.000Z"):
    """Minimal event page reusing a real event object under a new id."""
    event, _, _ = partiful.parse_event_page(EVENT_HTML)
    props = {
        "event": dict(event, id=event_id, title=f"Event {event_id}", startDate=start),
        "similarEvents": [{"id": s, "startDate": start, "title": s} for s in similar_ids],
        "similarEventsRegion": region,
    }
    return ('<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": props}}) + "</script>")


def test_crawl_follows_similar_events_within_region_up_to_cap():
    pages = {
        "https://partiful.com/e/A": _event_page("A", ["B", "C"]),
        "https://partiful.com/e/B": _event_page("B", ["A", "D"]),
        "https://partiful.com/e/C": _event_page("C", [], region="NYC"),
        "https://partiful.com/e/D": _event_page("D", ["E"]),
        "https://partiful.com/e/E": _event_page("E", []),
    }
    fetched = []

    def fetch(url):
        fetched.append(url)
        return pages[url]

    seeds = [dict(partiful.parse_event_page(pages["https://partiful.com/e/A"])[0])]
    events = partiful.crawl(seeds, fetch, max_pages=4, now=NOW, delay_s=0)
    ids = [e.url.rsplit("/", 1)[1] for e in events]
    assert ids == ["A", "B", "D"]  # C is another region; E is past the page cap
    assert len(fetched) == 4 and len(set(fetched)) == 4


def test_crawl_skips_similar_events_that_already_started():
    pages = {"https://partiful.com/e/A": _event_page("A", ["OLD"])}
    pages["https://partiful.com/e/A"] = pages["https://partiful.com/e/A"].replace(
        '"startDate": "2026-10-10T18:00:00.000Z", "title": "OLD"',
        '"startDate": "2026-09-01T18:00:00.000Z", "title": "OLD"')
    fetched = []
    seeds = [partiful.parse_event_page(pages["https://partiful.com/e/A"])[0]]
    partiful.crawl(seeds, lambda u: fetched.append(u) or pages[u], max_pages=10, now=NOW, delay_s=0)
    assert fetched == ["https://partiful.com/e/A"]


def test_crawl_survives_a_failing_event_page():
    def fetch(url):
        raise RuntimeError("503")

    seeds = _explore_events()[:2]
    events = partiful.crawl(seeds, fetch, max_pages=5, now=NOW, delay_s=0)
    assert len(events) == 2  # seeds still become events from explore data


def test_next_data_is_robust_to_missing_script():
    assert partiful.page_props("<html></html>") == {}
    assert re.search("__NEXT_DATA__", EXPLORE_HTML)
