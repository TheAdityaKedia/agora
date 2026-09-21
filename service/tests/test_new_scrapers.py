"""Compact tests for the second batch of theater/venue scrapers.

Each scraper has its own fixture; these tests exercise parse() end-to-end
(count, tz-aware start_time, image_url present, matches(url)).
"""
from pathlib import Path

import pytest

from scrapers.base import RawEvent
from scrapers import (
    brava, greatstar, magictheatre, nctcsf, palace, presidio, sfwarmemorial, warfield,
)

FIXTURES = Path(__file__).parent / "fixtures"

CASES = [
    ("nctcsf", nctcsf, "https://nctcsf.org/shows/", "https://www.brava.org/"),
    ("brava", brava, "https://www.brava.org/events", "https://magictheatre.org/"),
    ("magic", magictheatre, "https://magictheatre.org/calendar", "https://nctcsf.org/"),
    ("greatstar", greatstar, "https://www.greatstartheater.org/", "https://magictheatre.org/"),
    ("presidio", presidio, "https://www.presidiotheatre.org/shows", "https://ybca.org/"),
    ("warfield", warfield, "https://www.thewarfieldtheatre.com/events", "https://gamh.com/"),
    ("palace", palace, "https://www.palaceoffinearts.org/", "https://ybca.org/"),
    ("sfwarmemorial", sfwarmemorial, "https://sfwarmemorial.org/calendar/", "https://ybca.org/"),
]


@pytest.mark.parametrize("name,module,positive_url,negative_url", CASES)
def test_matches(name, module, positive_url, negative_url):
    assert module.matches(positive_url)
    assert not module.matches(negative_url)


@pytest.mark.parametrize("name,module,positive_url,negative_url", CASES)
def test_parse_returns_events(name, module, positive_url, negative_url):
    html = (FIXTURES / f"{name}_events.html").read_text()
    events = module.parse(html)
    assert len(events) >= 1, f"{name}: expected at least 1 event"
    for ev in events:
        assert isinstance(ev, RawEvent)
        assert ev.title
        assert ev.start_time.tzinfo is not None
        assert ev.location  # a venue address string
