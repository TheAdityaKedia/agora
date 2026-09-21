from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapers.base import RawEvent
from scrapers.fillmore import parse, matches, VENUE

FIXTURE = Path(__file__).parent / "fixtures" / "fillmore_events.html"


@pytest.fixture
def html():
    return FIXTURE.read_text()


def test_matches():
    assert matches("https://www.thefillmore.com/shows")
    assert not matches("https://gamh.com/calendar/")


def test_parse_extracts_music_events(html):
    events = parse(html)
    assert len(events) >= 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_event_has_isotime_and_utc(html):
    ev = parse(html)[0]
    assert ev.start_time.tzinfo == timezone.utc
    # Cross-check that a known Fillmore fixture time (Sep 21 8pm PT = 03:00 UTC Sep 22)
    # falls within a sane 2026 window.
    assert 2026 <= ev.start_time.year <= 2027


def test_event_url_points_at_ticketmaster(html):
    ev = parse(html)[0]
    assert ev.url and "ticketmaster.com" in ev.url


def test_event_image_url_present(html):
    for ev in parse(html):
        assert ev.image_url and ev.image_url.startswith("https://")


def test_location_defaults_to_full_address_when_source_gives_bare_name(html):
    for ev in parse(html):
        # The Fillmore's location.name is just "The Fillmore" — we upgrade it.
        assert ev.location == VENUE
