from pathlib import Path

import pytest

from scrapers.sfwarmemorial import parse_event_description, matches


DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "sfwarmemorial_detail.html"


@pytest.fixture
def detail_html():
    return DETAIL_FIXTURE.read_text()


def test_matches():
    assert matches("https://sfwarmemorial.org/event-detail/?eventId=124627")
    assert not matches("https://gamh.com/")


def test_parse_event_description_extracts_event_info(detail_html):
    """The synopsis is .event-information .event-info; the 'Event Information'
    header label is excluded."""
    desc = parse_event_description(detail_html)
    assert desc and desc.startswith("He accepts the chance to lead the Republic of Genoa")
    assert "Event Information" not in desc


def test_parse_event_description_none_when_absent():
    assert parse_event_description("<html><body><p>no info block</p></body></html>") is None
