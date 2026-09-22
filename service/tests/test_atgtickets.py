from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.atgtickets import (
    parse,
    parse_performances,
    matches,
    _parse_date_range,
    _parse_day,
    DEFAULT_HOUR,
)

FIXTURE = Path(__file__).parent / "fixtures" / "atgtickets_events.html"
DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "atg_detail.html"
UTC = ZoneInfo("UTC")
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return FIXTURE.read_text()


def test_matches():
    assert matches("https://us.atgtickets.com/whats-on/san-francisco/")
    assert not matches("https://greenapplebooks.com/events")


def test_parse_returns_two_events(html):
    events = parse(html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_single_date_card(html):
    ev = parse(html)[0]
    assert ev.title  # e.g. "Laurie Anderson"
    assert ev.url and ev.url.startswith("https://us.atgtickets.com/events/")
    assert ev.location  # venue name like "Curran Theatre"
    # Description carries the displayed date and genre bits
    assert ev.description
    # Start time defaults to 7 PM SF-local
    assert ev.start_time.tzinfo is not None
    local = ev.start_time.astimezone(PACIFIC)
    assert local.hour == DEFAULT_HOUR
    assert local.year == 2026


def test_parse_multiday_card_uses_start_date(html):
    """For a 'Sat Sep 26 - Sun Sep 27, 2026' card, start_time is Sep 26 @ 7pm."""
    ev = parse(html)[1]
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day) == (2026, 9, 26)
    assert local.hour == DEFAULT_HOUR
    # The full range should be preserved in the description
    assert "Sep 26" in ev.description
    assert "Sep 27" in ev.description


def test_parse_date_range_single_day():
    r = _parse_date_range("Fri, Sep 25, 2026")
    assert r == (date(2026, 9, 25), date(2026, 9, 25))


def test_parse_date_range_year_only_at_end():
    """Left side has no year; year is inferred from the right side."""
    r = _parse_date_range("Sat, Sep 26 - Sun, Sep 27, 2026")
    assert r == (date(2026, 9, 26), date(2026, 9, 27))


def test_parse_date_range_year_on_both_sides():
    r = _parse_date_range("Sat, Dec 30, 2026 - Sun, Jan 3, 2027")
    assert r == (date(2026, 12, 30), date(2027, 1, 3))


def test_parse_date_range_cross_year_only_end_year():
    """Left has no year; the parsed left ends up after the right → assume previous year."""
    r = _parse_date_range("Sat, Dec 30 - Sun, Jan 3, 2027")
    assert r == (date(2026, 12, 30), date(2027, 1, 3))


def test_parse_date_range_rejects_garbage():
    assert _parse_date_range("Coming soon") is None
    assert _parse_date_range("") is None


def test_parse_day_uses_fallback_year_when_missing():
    assert _parse_day("Sat, Sep 26", 2028) == date(2028, 9, 26)


def test_parse_extracts_image_url(html):
    for ev in parse(html):
        assert ev.image_url and ev.image_url.startswith("https://res.cloudinary.com/dwzhqvxaz/")


# --- Per-performance parsing (detail page JSON-LD subEvent[]) ---

def _show():
    return RawEvent(
        title="Bluey's Big Play",
        start_time=datetime(2026, 9, 26, 19, tzinfo=PACIFIC),
        location="Orpheum Theatre",
        url="https://us.atgtickets.com/events/blueys-big-play/orpheum-theatre/",
        description="Family · Sat, Sep 26 - Sun, Sep 27, 2026",
        image_url="https://res.cloudinary.com/dwzhqvxaz/poster.jpg",
    )


@pytest.fixture
def detail_html():
    return DETAIL_FIXTURE.read_text()


def test_parse_performances_one_event_per_subevent(detail_html):
    events = parse_performances(detail_html, show=_show())
    assert len(events) == 3
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_performances_uses_absolute_startdate(detail_html):
    """subEvent startDate '2026-09-26T17:00:00.000Z' → exact UTC, no year guessing."""
    first = parse_performances(detail_html, show=_show())[0]
    assert first.start_time.astimezone(UTC) == datetime(2026, 9, 26, 17, 0, tzinfo=UTC)


def test_parse_performances_uses_show_detail_url_not_ticket_link(detail_html):
    """Every performance links to the show detail page (from the top-level
    TheaterEvent JSON-LD `url`), not the per-seat ticketing deep link
    (…/tickets/<guid>/), which isn't a useful browsing landing page."""
    urls = [e.url for e in parse_performances(detail_html, show=_show())]
    assert urls == [
        "https://us.atgtickets.com/events/blueys-big-play/orpheum-theatre/"
    ] * 3
    assert all("/tickets/" not in u for u in urls)


def test_parse_performances_uses_jsonld_description(detail_html):
    """A real synopsis from the detail page's TheaterEvent JSON-LD `description`
    replaces the thin genre/date listing blurb, with HTML entities decoded."""
    ev = parse_performances(detail_html, show=_show())[0]
    assert ev.description.startswith(
        "Bluey's Big Play comes back to Orpheum Theatre"
    )
    assert "&apos;" not in ev.description  # entities decoded
    assert ev.description != _show().description  # not the listing blurb


def test_parse_performances_carries_show_title_and_image(detail_html):
    ev = parse_performances(detail_html, show=_show())[0]
    assert ev.title == "Bluey's Big Play"
    assert ev.image_url == "https://res.cloudinary.com/dwzhqvxaz/poster.jpg"


def test_parse_performances_location_from_subevent(detail_html):
    ev = parse_performances(detail_html, show=_show())[0]
    assert "Orpheum Theatre" in ev.location
    assert "1192 Market St" in ev.location


def test_parse_performances_single_night_uses_toplevel_startdate():
    """A show with no subEvent[] falls back to the top-level TheaterEvent time,
    and uses the top-level `url` as its detail page."""
    html = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"TheaterEvent","name":"Laurie Anderson",'
        '"url":"https://us.atgtickets.com/events/laurie-anderson/curran-theater/",'
        '"description":"Laurie Anderson: The Republic of Love with Sexmob comes to Curran Theatre.",'
        '"startDate":"2026-09-26T03:00:00.000Z",'
        '"location":{"@type":"PerformingArtsTheater","name":"Curran Theatre",'
        '"address":{"streetAddress":"445 Geary St"}}}'
        '</script>'
    )
    events = parse_performances(html, show=_show())
    assert len(events) == 1
    assert events[0].start_time.astimezone(UTC) == datetime(2026, 9, 26, 3, 0, tzinfo=UTC)
    assert events[0].url == "https://us.atgtickets.com/events/laurie-anderson/curran-theater/"
    assert events[0].description.startswith("Laurie Anderson: The Republic of Love")


def test_parse_performances_description_and_url_fall_back_to_show():
    """When the JSON-LD carries no description/url, fall back to the show's own
    listing blurb and card URL rather than dropping them."""
    html = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"TheaterEvent","name":"X",'
        '"startDate":"2026-09-26T03:00:00.000Z"}'
        '</script>'
    )
    ev = parse_performances(html, show=_show())[0]
    assert ev.description == _show().description
    assert ev.url == _show().url


def test_parse_performances_empty_when_no_theater_event():
    assert parse_performances("<html><body>no json-ld</body></html>", show=_show()) == []
