from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.actsf import (
    parse,
    parse_performances,
    parse_show_description,
    matches,
    _parse_date_range,
    _parse_month_day,
    DEFAULT_HOUR,
    VENUE,
)


FIXTURE = Path(__file__).parent / "fixtures" / "actsf_events.html"
PERF_FIXTURE = Path(__file__).parent / "fixtures" / "actsf_performances.html"
DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "actsf_detail.html"
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return FIXTURE.read_text()


@pytest.fixture
def perf_html():
    return PERF_FIXTURE.read_text()


@pytest.fixture
def detail_html():
    return DETAIL_FIXTURE.read_text()


def test_matches():
    assert matches("https://www.act-sf.org/whats-on")
    assert not matches("https://greenapplebooks.com/events")


def test_parse_returns_three_events(html):
    events = parse(html)
    assert len(events) == 3
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_cross_month_range_uses_start_date(html):
    """First fixture card: 'SEP 22–OCT 18, 2026' → start on Sep 22 @ 7pm."""
    ev = parse(html)[0]
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day) == (2026, 9, 22)
    assert local.hour == DEFAULT_HOUR
    assert "SEP 22" in ev.description
    assert "OCT 18" in ev.description
    assert ev.location == VENUE
    assert ev.url and ev.url.startswith("https://www.act-sf.org/")


def test_parse_same_month_shorthand(html):
    """Second fixture card: 'MAR 10-27, 2027' → start on Mar 10, 2027."""
    ev = parse(html)[1]
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day) == (2027, 3, 10)


def test_parse_single_day(html):
    """Third fixture card: 'OCT 21, 2026' → single day."""
    ev = parse(html)[2]
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day) == (2026, 10, 21)


# --- Date parser unit tests, exercising all real formats ---

def test_date_range_en_dash():
    assert _parse_date_range("SEP 22–OCT 18, 2026") == (date(2026, 9, 22), date(2026, 10, 18))


def test_date_range_em_dash():
    assert _parse_date_range("NOV 12—DEC 6, 2026") == (date(2026, 11, 12), date(2026, 12, 6))


def test_date_range_ascii_hyphen():
    assert _parse_date_range("MAY 13-JUN 13, 2027") == (date(2027, 5, 13), date(2027, 6, 13))


def test_date_range_same_month_shorthand():
    assert _parse_date_range("MAR 10-27, 2027") == (date(2027, 3, 10), date(2027, 3, 27))


def test_date_range_single_day():
    assert _parse_date_range("OCT 21, 2026") == (date(2026, 10, 21), date(2026, 10, 21))


def test_date_range_year_wrap_dec_to_jan():
    """A 'DEC 20-JAN 5, 2028' range means start was 2027."""
    assert _parse_date_range("DEC 20-JAN 5, 2028") == (date(2027, 12, 20), date(2028, 1, 5))


def test_date_range_rejects_garbage():
    assert _parse_date_range("Coming soon") is None
    assert _parse_date_range("") is None
    assert _parse_date_range("2026") is None


def test_parse_month_day_case_insensitive():
    assert _parse_month_day("sep 22") == (9, 22)
    assert _parse_month_day("SEP 22") == (9, 22)
    assert _parse_month_day("Sep 22") == (9, 22)


def test_parse_extracts_image_url(html):
    for ev in parse(html):
        assert ev.image_url and ev.image_url.startswith("https://res.cloudinary.com/a-c-t/")


# --- Per-performance parsing (the /performances page) ---

RUN_START = date(2026, 9, 22)
RUN_END = date(2026, 10, 18)


SHOW_URL = "https://www.act-sf.org/whats-on/2026-27-season/north-by-northwest"


def _perf(perf_html, **overrides):
    kwargs = dict(
        title="Alfred Hitchcock's North by Northwest",
        run_start=RUN_START,
        run_end=RUN_END,
        location=VENUE,
        url=SHOW_URL,
        image_url="https://res.cloudinary.com/a-c-t/poster.jpg",
    )
    kwargs.update(overrides)
    return parse_performances(perf_html, **kwargs)


def test_parse_performances_one_event_per_showing(perf_html):
    events = _perf(perf_html)
    assert len(events) == 5
    assert all(isinstance(e, RawEvent) for e in events)


def test_parse_performances_extracts_date_and_time(perf_html):
    """First row: 'Tue Sep 22 at 06:30PM' → Sep 22 2026, 18:30 SF-local."""
    ev = _perf(perf_html)[0]
    local = ev.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day, local.hour, local.minute) == (2026, 9, 22, 18, 30)


def test_parse_performances_same_day_two_showings(perf_html):
    """Sep 26 has a 2:00PM matinee and an 8:00PM evening — two distinct events."""
    sep26 = [e for e in _perf(perf_html) if e.start_time.astimezone(PACIFIC).day == 26]
    assert len(sep26) == 2
    hours = sorted(e.start_time.astimezone(PACIFIC).hour for e in sep26)
    assert hours == [14, 20]


def test_parse_performances_crosses_month_within_run(perf_html):
    """'Sun Oct 18' resolves to Oct 2026 from the run range, not Sep."""
    last = _perf(perf_html)[-1]
    local = last.start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day) == (2026, 10, 18)


def test_parse_performances_use_show_url_not_seat_selection(perf_html):
    """Every performance links to the show's detail page, not the per-seat
    ticketing deep link (secure.act-sf.org/...), which is not a useful landing
    page for browsing.
    """
    for ev in _perf(perf_html):
        assert ev.url == SHOW_URL


def test_parse_performances_carries_title_location_image(perf_html):
    ev = _perf(perf_html)[0]
    assert ev.title == "Alfred Hitchcock's North by Northwest"
    assert ev.location == VENUE
    assert ev.image_url == "https://res.cloudinary.com/a-c-t/poster.jpg"


def test_parse_performances_night_content_in_description(perf_html):
    ev = _perf(perf_html)[0]
    assert ev.description and "Preview" in ev.description


def test_parse_performances_year_wraps_dec_to_jan():
    """A Jan performance in a Dec→Jan run resolves to the next year."""
    html = (
        '<ul class="performance-list">'
        '<li class="performance-list__item"><a class="performance" href="https://secure.act-sf.org/1/2">'
        '<h3 class="performance__date-time"><span class="date">Sat Jan 3</span> at '
        '<span class="time">02:00PM</span></h3></a></li></ul>'
    )
    events = parse_performances(
        html,
        title="New Year Show",
        run_start=date(2027, 12, 20),
        run_end=date(2028, 1, 5),
        location=VENUE,
        url="https://www.act-sf.org/whats-on/new-year-show",
        image_url=None,
    )
    local = events[0].start_time.astimezone(PACIFIC)
    assert (local.year, local.month, local.day) == (2028, 1, 3)


def test_parse_performances_empty_when_no_rows():
    assert _perf("<ul class='performance-list'></ul>") == []


# --- Show synopsis parsing (the show detail page, show.url) ---

def test_parse_show_description_returns_synopsis(detail_html):
    desc = parse_show_description(detail_html)
    assert desc is not None
    # Opening line of the real synopsis.
    assert desc.startswith("Be transported to San Francisco")
    # All three synopsis paragraphs are joined into one blurb.
    assert "Harlem of the West" in desc
    assert "displacement, gentrification" in desc
    assert "SFBATCO" in desc


def test_parse_show_description_skips_credits_and_notices(detail_html):
    """The credits block (no <p>) and the subscription-notice block precede the
    synopsis but must not leak into it."""
    desc = parse_show_description(detail_html)
    assert "BOOK BY MICHAEL GENE SULLIVAN" not in desc
    assert "no longer available" not in desc
    assert "Exchange information" not in desc


def test_parse_show_description_strips_trailing_logistics_and_quotes(detail_html):
    """The trailing block (a director quote + a 'Runs approximately' run-time
    line) is not the synopsis and must not be returned."""
    desc = parse_show_description(detail_html)
    assert "Runs approximately" not in desc
    assert "the director" not in desc


def test_parse_show_description_none_when_absent():
    assert parse_show_description("<html><body><p>Buy tickets</p></body></html>") is None
    assert parse_show_description("") is None


def test_parse_performances_description_override(perf_html):
    """When a synopsis is supplied it becomes the description for every
    performance, replacing the per-row ticketing keywords."""
    synopsis = "A real show synopsis about the Fillmore District."
    events = _perf(perf_html, description=synopsis)
    assert events
    assert all(e.description == synopsis for e in events)


def test_parse_performances_falls_back_to_keywords_without_synopsis(perf_html):
    """With no synopsis passed (description=None), the existing per-row keyword
    description is preserved so nothing regresses."""
    ev = _perf(perf_html)[0]
    assert ev.description and "Preview" in ev.description


# --- Orchestration: run-level fallback keeps the synopsis ---

def _run_level_show():
    return RawEvent(
        title="Every Saturday Night",
        start_time=datetime(2025, 10, 3, 19, tzinfo=PACIFIC),
        location=VENUE,
        url="https://www.act-sf.org/whats-on/limited-engagements/every-saturday-night",
        description="OCT 3–NOV 2, 2025",
        image_url=None,
    )


def test_scrape_show_no_rows_emits_run_level_event_with_synopsis(monkeypatch):
    """A show whose /performances page has no rows still gets a single run-level
    event carrying the synopsis (not the date-range fallback)."""
    from scrapers import actsf

    monkeypatch.setattr(actsf, "_fetch_show_synopsis", lambda url: "A real synopsis blurb.")
    monkeypatch.setattr(actsf, "load_page_html", lambda *a, **k: "<ul class='performance-list'></ul>")

    events = actsf._scrape_show_performances(object(), _run_level_show())
    assert len(events) == 1
    assert events[0].description == "A real synopsis blurb."
    assert events[0].url == _run_level_show().url


def test_scrape_show_no_rows_no_synopsis_defers_to_caller(monkeypatch):
    """No rows and no synopsis → return [] so the caller keeps the original
    run-level (date-range) event; nothing regresses."""
    from scrapers import actsf

    monkeypatch.setattr(actsf, "_fetch_show_synopsis", lambda url: None)
    monkeypatch.setattr(actsf, "load_page_html", lambda *a, **k: "<ul class='performance-list'></ul>")

    assert actsf._scrape_show_performances(object(), _run_level_show()) == []
