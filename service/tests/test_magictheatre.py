from pathlib import Path

import pytest

from scrapers.magictheatre import parse_event_description, matches


DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "magictheatre_detail.html"


@pytest.fixture
def detail_html():
    return DETAIL_FIXTURE.read_text()


def test_matches():
    assert matches("https://magictheatre.org/calendar/a-rashomon")
    assert not matches("https://gamh.com/")


def test_parse_event_description_takes_substantial_paragraphs(detail_html):
    """The synopsis is the long .eventitem-column-content paragraph; short
    credit lines ('By …', 'Directed by …') and the booking note are excluded."""
    desc = parse_event_description(detail_html)
    assert desc and desc.startswith("“a Rashomon” for San Francisco")
    assert "Directed by" not in desc
    assert "Performance Pass" not in desc


def test_parse_event_description_none_when_no_substantial_text():
    html = '<div class="eventitem-column-content"><p>By X</p><p>Tickets only</p></div>'
    assert parse_event_description(html) is None
