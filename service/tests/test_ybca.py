from pathlib import Path

import pytest

from scrapers.ybca import parse_event_description, matches


DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "ybca_detail.html"


@pytest.fixture
def detail_html():
    return DETAIL_FIXTURE.read_text()


def test_matches():
    assert matches("https://ybca.org/event/x")
    assert not matches("https://gamh.com/")


def test_parse_event_description_uses_left_content(detail_html):
    """The blurb is the .left-content paragraphs; the .section-wrapper funding
    credits are excluded."""
    desc = parse_event_description(detail_html)
    assert desc and desc.startswith("Come and make an impression")
    assert "Bloomberg Philanthropies" not in desc


def test_parse_event_description_falls_back_to_og():
    html = '<html><head><meta property="og:description" content="Short summary."></head><body></body></html>'
    assert parse_event_description(html) == "Short summary."


def test_parse_event_description_none_when_nothing():
    assert parse_event_description("<html><body><p>x</p></body></html>") is None
