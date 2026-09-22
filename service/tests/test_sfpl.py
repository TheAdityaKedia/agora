from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.sfpl import (
    parse, matches, _parse_start, _infer_ampm, _last_page_number,
    parse_event_description,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sfpl_events.html"
PACIFIC = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def html():
    return FIXTURE.read_text()


def test_matches():
    assert matches("https://sfpl.org/events")
    assert matches("https://sfpl.org/events/2026/09/21/some-slug")
    assert not matches("https://sfjazz.org/calendar/")


def test_parse_returns_events(html):
    # The fixture has 3 cards, one of which is a
    # babies-toddlers-or-preschoolers early-learning session — filtered out.
    events = parse(html)
    assert len(events) == 2
    assert all(isinstance(e, RawEvent) for e in events)
    assert all("Early Learning" not in e.title for e in events)


def test_parse_fields(html):
    ev = parse(html)[0]  # "Tutorial: Meet One-on-One with a Financial Counselor"
    assert ev.title.startswith("Tutorial")
    assert ev.url and ev.url.startswith("https://sfpl.org/events/")
    assert ev.location.startswith("SFPL — ")
    assert ev.image_url and ev.image_url.startswith("https://sfpl.org/")
    # description carries the date/audience/topics
    assert ev.description
    assert "Adults" in ev.description


def test_parse_start_time_tz_aware(html):
    for ev in parse(html):
        assert ev.start_time.tzinfo is not None


def test_infer_ampm_defaults_morning_hours_to_am():
    # 10:15 storytime → 10 AM
    assert _infer_ampm(10) == 10
    assert _infer_ampm(11) == 11
    # 9 AM tutorial start
    assert _infer_ampm(9) == 9
    # 7 AM edge (rare, treated AM)
    assert _infer_ampm(7) == 7


def test_infer_ampm_afternoon_hours_go_pm():
    # 4:00 bookmobile → 4 PM (16:00)
    assert _infer_ampm(4) == 16
    # 5:30 book club → 5 PM (17:00)
    assert _infer_ampm(5) == 17
    assert _infer_ampm(6) == 18
    assert _infer_ampm(1) == 13
    # noon stays noon
    assert _infer_ampm(12) == 12


def test_parse_start_produces_utc_from_pacific():
    """'9:00 - 5:00' start → 9:00 AM PT → 16:00 UTC (PDT offset)."""
    dt = _parse_start("Tuesday, 9/22/2026, 9:00 - 5:00")
    assert dt is not None
    local = dt.astimezone(PACIFIC)
    assert (local.year, local.month, local.day, local.hour, local.minute) == (2026, 9, 22, 9, 0)
    assert dt.tzinfo == timezone.utc


def test_parse_start_afternoon_program():
    """'4:00 - 7:00' start → 4:00 PM PT."""
    dt = _parse_start("Monday, 9/21/2026, 4:00 - 7:00")
    local = dt.astimezone(PACIFIC)
    assert (local.hour, local.minute) == (16, 0)


def test_parse_start_rejects_garbage():
    assert _parse_start("no date here") is None
    assert _parse_start("") is None


def test_last_page_number_reads_pagination():
    html = '''
    <ul>
      <li><a href="?page=0">Current</a></li>
      <li><a href="?page=1">next › Next page</a></li>
      <li><a href="?date=x&page=83">last » Last page</a></li>
    </ul>
    '''
    assert _last_page_number(html) == 83


def test_last_page_number_missing_returns_none():
    assert _last_page_number("<html><body>no pager</body></html>") is None


# --- kid-only audience filter -------------------------------------------

_SFPL_CARD_TEMPLATE = """
<article class="event event--teaser {audience_classes} teaser">
  <div class="event__details"><div class="event__main">
    <header class="event__header">
      <div class="event__date"><span class="date-display-range">
        Tuesday, 9/22/2026, 10:15 - 10:45
      </span></div>
      <div class="event__name"><h2 class="event__title">
        <a href="/events/2026/09/22/test-event">Test Event</a>
      </h2></div>
    </header>
    <div class="event__location">Main</div>
  </div></div>
</article>
"""


def _one_card(audience: str) -> str:
    return f'<div>{_SFPL_CARD_TEMPLATE.format(audience_classes=audience)}</div>'


def test_kid_only_audiences_are_skipped():
    for aud in (
        "event--babies-toddlers-or-preschoolers",
        "event--elementary-school-age",
        "event--middle-school-age",
    ):
        assert parse(_one_card(aud)) == [], f"expected skip for {aud}"


def test_adult_relevant_audiences_are_kept():
    for aud in ("event--adults", "event--teens", "event--all-ages", "event--families"):
        events = parse(_one_card(aud))
        assert len(events) == 1, f"expected keep for {aud}"


def test_parse_event_description_prefers_og():
    html = """
    <html><head>
      <meta name="description" content="Short summary from meta name.">
      <meta property="og:description" content="Longer OpenGraph description that
        we want to prefer.">
    </head><body></body></html>
    """
    d = parse_event_description(html)
    assert d is not None
    assert d.startswith("Longer OpenGraph")


def test_parse_event_description_falls_back_to_meta_name():
    html = '<html><head><meta name="description" content="Only meta name."></head><body></body></html>'
    assert parse_event_description(html) == "Only meta name."


def test_parse_event_description_none_when_missing():
    assert parse_event_description("<html><body>no meta</body></html>") is None


def test_mixed_kid_and_adult_audience_is_kept():
    """A card with both a kid audience and an adult-relevant audience stays.

    Some SFPL programming is co-audience (all-ages + children); we should not
    drop it just because a kid class is present.
    """
    mixed = "event--all-ages event--elementary-school-age"
    events = parse(_one_card(mixed))
    assert len(events) == 1
