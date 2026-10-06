import json
from datetime import datetime, timezone
from pathlib import Path

from scrapers import spektrix, stanfordlive

FIXTURE = Path(__file__).parent / "fixtures" / "spektrix_stanfordlive.json"


def _load():
    d = json.loads(FIXTURE.read_text())
    return d["events"], d["instances"]


def test_one_event_per_performance_in_utc():
    events, instances = _load()
    out = spektrix.parse_events(events, instances, fallback_location="HALL")
    assert len(out) == len(instances)
    first = out[0]
    assert first.start_time.tzinfo is not None
    assert first.location == "HALL"
    assert [e.start_time for e in out] == sorted(e.start_time for e in out)
    marley = [e for e in out if e.title.startswith("Bob Marley")]
    assert marley and marley[0].url.startswith("https://live.stanford.edu/")
    assert marley[0].description


def test_cancelled_and_unknown_instances_dropped():
    events, instances = _load()
    instances = [dict(instances[0], cancelled=True),
                 {"event": {"id": "nope"}, "startUtc": "2026-10-09T23:00:00", "cancelled": False}]
    assert spektrix.parse_events(events, instances, fallback_location="X") == []


def test_parse_utc():
    assert spektrix._parse_utc("2026-10-09T23:00:00") == datetime(2026, 10, 9, 23, tzinfo=timezone.utc)
    assert spektrix._parse_utc("2026-10-09T23:00:00Z") == datetime(2026, 10, 9, 23, tzinfo=timezone.utc)
    assert spektrix._parse_utc(None) is None


def test_stanfordlive_drops_student_allocations_and_links_pageless_events():
    events, instances = _load()
    raw = spektrix.parse_events(events, instances, fallback_location=stanfordlive.ADDRESS)
    out = stanfordlive.public(raw)
    assert not any("Student Lottery" in e.title for e in out)
    assert any("Student Lottery" in e.title for e in raw)  # the fixture has one
    assert all(e.url for e in out)
    assert stanfordlive.matches("https://live.stanford.edu/calendar")
