import json
from datetime import datetime, timezone
from pathlib import Path

from scrapers import diasporaarts, f8, squarespace_events as sq

FIXTURES = Path(__file__).parent / "fixtures"


def test_json_feed_gives_exact_times_venue_and_raw_titles():
    data = json.loads((FIXTURES / "squarespace_f8.json").read_text())
    events = sq.parse_json_events(data, base_url=f8.CALENDAR_URL, fallback_location="FALLBACK")
    assert len(events) == 2
    ev = events[0]
    assert ev.title == data["upcoming"][0]["title"].strip()  # "~" kept, not cut
    assert ev.start_time == datetime.fromtimestamp(data["upcoming"][0]["startDate"] // 1000, timezone.utc)
    assert ev.start_time.hour == 4  # 9pm PDT, not midnight
    assert ev.location.startswith("F8, 1192 Folsom")
    assert ev.url.startswith("https://www.feightsf.com/new-events/")
    assert ev.image_url.startswith("https://images.squarespace-cdn.com/")
    assert ev.description and "<" not in ev.description


def test_json_feed_skips_items_without_title_or_start():
    data = {"upcoming": [{"title": "", "startDate": 1}, {"title": "X"}]}
    assert sq.parse_json_events(data, base_url="https://x.org/events") == []


def test_diaspora_keeps_bay_area_only(monkeypatch):
    data = json.loads((FIXTURES / "squarespace_diaspora.json").read_text())
    monkeypatch.setattr(sq, "scrape_json",
                        lambda url, **kw: sq.parse_json_events(data, base_url=url))
    out = diasporaarts.scrape()
    assert [e.title for e in out] == [data["upcoming"][1]["title"].strip()]
    assert "San Francisco" in out[0].location


def test_wrappers():
    assert f8.matches("https://www.feightsf.com/new-events")
    assert diasporaarts.matches("https://www.diasporaartsconnection.org/events")
