from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.presidio import (
    parse,
    parse_performances,
    matches,
    DEFAULT_HOUR,
    DEFAULT_MINUTE,
    VENUE,
)

FIXTURES = Path(__file__).parent / "fixtures"
LISTING_FIXTURE = FIXTURES / "presidio_listing.html"
DETAIL_FIXTURE = FIXTURES / "presidio_detail.html"
UTC = ZoneInfo("UTC")
PACIFIC = ZoneInfo("America/Los_Angeles")

SHOW_URL = "https://www.presidiotheatre.org/show-details/paul-taylor-dance-company"
# The first substantial paragraph of the detail page's `div.text-nrml` prose
# block (the leading title line and short advisory blurbs are skipped).
EXPECTED_SYNOPSIS = (
    "Paul Taylor Dance Company brings its innovative modern dance repertory to "
    "the Presidio Theatre for a rare three-night San Francisco engagement, "
    "performing beloved classics alongside bold new works from the company's "
    "celebrated founder."
)


@pytest.fixture
def listing_html():
    return LISTING_FIXTURE.read_text()


@pytest.fixture
def detail_html():
    return DETAIL_FIXTURE.read_text()


def test_matches():
    assert matches("https://www.presidiotheatre.org/shows")
    assert not matches("https://ybca.org/")


# --- Listing (run-level shows) ---

def test_parse_returns_run_level_shows(listing_html):
    events = parse(listing_html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_single_date_card(listing_html):
    ev = parse(listing_html)[0]
    assert ev.title == "The History of Basque Music: A Living Portrait of an Ancient Land"
    assert ev.url and ev.url.startswith("https://www.presidiotheatre.org/show-details/")
    assert ev.location == VENUE
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day) == (2026, 9, 26)
    assert (local.hour, local.minute) == (DEFAULT_HOUR, DEFAULT_MINUTE)
    assert ev.image_url and ev.image_url.endswith(".webp")


def test_parse_range_card_uses_start_date(listing_html):
    """A 'May 20, 2027 - May 22, 2027' card must not be dropped; it uses May 20."""
    ev = parse(listing_html)[1]
    assert ev.title == "Paul Taylor Dance Company"
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day) == (2027, 5, 20)
    # Full displayed range preserved in the description.
    assert "May 20" in ev.description
    assert "May 22" in ev.description


# --- Per-performance parsing (detail page JSON-LD offers[]) ---

def _show():
    return RawEvent(
        title="Paul Taylor Dance Company",
        start_time=datetime(2027, 5, 20, DEFAULT_HOUR, DEFAULT_MINUTE, tzinfo=PACIFIC),
        location=VENUE,
        url=SHOW_URL,
        description="DANCE · May 20, 2027 - May 22, 2027",
        image_url="https://www.presidiotheatre.org/storage/poster.webp",
    )


def test_parse_performances_one_event_per_offer(detail_html):
    events = parse_performances(detail_html, show=_show())
    assert len(events) == 4
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_performances_uses_absolute_datetime(detail_html):
    """First offer '2027-05-20T19:30:00-07:00' → exact UTC, no year/time guessing."""
    first = parse_performances(detail_html, show=_show())[0]
    assert first.start_time.astimezone(UTC) == datetime(2027, 5, 21, 2, 30, tzinfo=UTC)


def test_parse_performances_same_day_two_showtimes(detail_html):
    """May 22 has a 2pm matinee and a 7:30pm evening — both distinct events."""
    events = parse_performances(detail_html, show=_show())
    may22 = [e.start_time.astimezone(PACIFIC) for e in events
             if e.start_time.astimezone(PACIFIC).day == 22]
    assert sorted((t.hour, t.minute) for t in may22) == [(14, 0), (19, 30)]


def test_parse_performances_use_show_url_not_ticket_link(detail_html):
    """Every performance links to the show's `/show-details/...` detail page,
    not the per-seat ticketing deep link (`ticket-path?EventInstanceId=...`),
    which is not a useful landing page for browsing.
    """
    events = parse_performances(detail_html, show=_show())
    for ev in events:
        assert ev.url == SHOW_URL
        assert "EventInstanceId=" not in ev.url


def test_parse_performances_carries_show_metadata(detail_html):
    ev = parse_performances(detail_html, show=_show())[0]
    assert ev.title == "Paul Taylor Dance Company"
    assert ev.location == VENUE
    assert ev.image_url == "https://www.presidiotheatre.org/storage/poster.webp"


# --- Descriptions (real synopsis from the detail page) ---

def test_parse_performances_uses_detail_page_synopsis(detail_html):
    """Description is the real synopsis from the detail page's prose block,
    not the placeholder 'CATEGORY · date'."""
    events = parse_performances(detail_html, show=_show())
    for ev in events:
        assert ev.description == EXPECTED_SYNOPSIS
    # The old placeholder must be gone.
    assert events[0].description != "DANCE · May 20, 2027 - May 22, 2027"


def test_parse_performances_synopsis_from_json_ld_description():
    """When there's no DOM prose block, fall back to the JSON-LD description."""
    html = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"TheaterEvent","name":"Lúnasa",'
        '"description":"Ireland\'s finest traditional band returns to the Presidio.",'
        '"offers":[{"@type":"Offer","validFrom":"2027-03-13T19:30:00-08:00",'
        '"url":"https://www.presidiotheatre.org/ticket-path?EventInstanceId=79803"}]}'
        '</script>'
    )
    ev = parse_performances(html, show=_show())[0]
    assert ev.description == "Ireland's finest traditional band returns to the Presidio."


def test_parse_performances_synopsis_falls_back_to_show_description():
    """No synopsis anywhere on the detail page → keep the run-level
    category/date description so a description is never dropped."""
    html = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"TheaterEvent","name":"TBD",'
        '"offers":[{"@type":"Offer","validFrom":"2027-03-13T19:30:00-08:00",'
        '"url":"https://www.presidiotheatre.org/ticket-path?EventInstanceId=79803"}]}'
        '</script>'
    )
    ev = parse_performances(html, show=_show())[0]
    assert ev.description == "DANCE · May 20, 2027 - May 22, 2027"


def test_parse_performances_single_offer_single_event():
    html = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"TheaterEvent","name":"Lúnasa",'
        '"offers":[{"@type":"Offer","validFrom":"2027-03-13T19:30:00-08:00",'
        '"url":"https://www.presidiotheatre.org/ticket-path?EventInstanceId=79803"}]}'
        '</script>'
    )
    events = parse_performances(html, show=_show())
    assert len(events) == 1
    assert events[0].start_time.astimezone(UTC) == datetime(2027, 3, 14, 3, 30, tzinfo=UTC)


def test_parse_performances_empty_when_no_theater_event():
    assert parse_performances("<html><body>no json-ld</body></html>", show=_show()) == []


def test_parse_performances_empty_when_no_offers():
    html = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"TheaterEvent","name":"TBD"}'
        '</script>'
    )
    assert parse_performances(html, show=_show()) == []
