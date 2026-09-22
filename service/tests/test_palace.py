from pathlib import Path

import pytest

from scrapers.palace import parse_event_description, matches


DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "palace_detail.html"


@pytest.fixture
def detail_html():
    return DETAIL_FIXTURE.read_text()


def test_matches():
    assert matches("https://www.palaceoffinearts.org/event/x")
    assert not matches("https://ybca.org/")


def test_parse_event_description_uses_first_richtext_block(detail_html):
    """The event blurb is the first .w-richtext block; later .w-richtext blocks
    are directions/parking and must not be picked."""
    desc = parse_event_description(detail_html)
    assert desc and desc.startswith("Kevin Fredericks (aka KevOnStage)")
    assert "Golden Gate Bridge" not in desc  # directions block excluded


def test_parse_event_description_none_when_no_richtext():
    assert parse_event_description("<html><body><p>nothing</p></body></html>") is None
