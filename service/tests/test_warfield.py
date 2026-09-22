from pathlib import Path

import pytest

from scrapers.warfield import parse_detail_description, matches


DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "warfield_detail.html"


@pytest.fixture
def detail_html():
    return DETAIL_FIXTURE.read_text()


def test_matches():
    assert matches("https://www.thewarfieldtheatre.com/events/detail/1099682")
    assert not matches("https://gamh.com/")


def test_parse_detail_description_extracts_bio(detail_html):
    """The event blurb lives in div.bio; extract it, drop the 'Artist
    Information' label and the ADA/ticketing-policy noise block."""
    desc = parse_detail_description(detail_html)
    assert desc is not None
    assert desc.startswith("Peter Hook first revisited")
    assert "Artist Information" not in desc          # heading label stripped
    assert "accessible section" not in desc          # .description noise excluded


def test_parse_detail_description_none_when_no_bio():
    assert parse_detail_description("<div class='event_detail'><p>no bio here</p></div>") is None
