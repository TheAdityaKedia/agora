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


def test_stanfordlive_header_image_from_srcset():
    html = ('<img class="page-item__image" src="https://x/related.jpg">'
            '<img loading="lazy" class="event-header__image" '
            'srcset="https://res.cloudinary.com/a/show-1440x1080?_a=B, https://res.cloudinary.com/a/show@2x 2x">')
    assert stanfordlive.header_image(html) == "https://res.cloudinary.com/a/show-1440x1080?_a=B"
    assert stanfordlive.header_image('<img class="page-item__image" src="x">') is None


def test_stanfordlive_hall_location():
    page = '<meta class="swiftype" name="venue_title" data-type="string" content="The Studio" />'
    assert stanfordlive.hall_location(page) == stanfordlive.HALLS["the studio"]
    assert stanfordlive.hall_location('<meta name="venue_title" content="Braun Rehearsal Hall">') == \
        f"Braun Rehearsal Hall, {stanfordlive.ADDRESS}"
    assert stanfordlive.hall_location("<p>no meta</p>") is None


def test_stanfordlive_add_page_details_fetches_each_page_once():
    from scrapers.base import RawEvent
    t = datetime(2026, 10, 9, tzinfo=timezone.utc)
    page = "https://live.stanford.edu/events/26-frost/show/"
    events = [RawEvent(title="Show", start_time=t, location="L", url=page, description=None),
              RawEvent(title="Show", start_time=t.replace(day=10), location="L", url=page, description=None),
              RawEvent(title="Messiah", start_time=t, location="L", url=stanfordlive.EVENTS_URL, description=None)]
    calls = []
    hall = stanfordlive.HALLS["frost amphitheater"]
    stanfordlive.add_page_details(events, fetch=lambda u: calls.append(u) or ("https://img/show.jpg", hall))
    assert calls == [page]  # once per page; the calendar fallback isn't fetched
    assert [e.image_url for e in events] == ["https://img/show.jpg", "https://img/show.jpg", None]
    assert [e.location for e in events] == [hall, hall, "L"]


def test_stanfordlive_add_page_details_keeps_fallback_when_page_fails():
    from scrapers.base import RawEvent
    t = datetime(2026, 10, 9, tzinfo=timezone.utc)
    events = [RawEvent(title="Show", start_time=t, location="L",
                       url="https://live.stanford.edu/events/x/", description=None)]
    stanfordlive.add_page_details(events, fetch=lambda u: (None, None))
    assert (events[0].image_url, events[0].location) == (None, "L")
