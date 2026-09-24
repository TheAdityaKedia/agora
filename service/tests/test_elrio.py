"""Tests for scrapers/elrio.py — El Rio (Mission District bar/venue).

El Rio's calendar is a Tockify embed (calname `elriosf2`); events come from
Tockify's `/api/ngevent` JSON. Fixture `elrio_ngevent.json` is a trimmed real
capture (4 representative events: a DJ/dance night, a recurring karaoke night,
a live salsa show with an HTML-entity in the title, and a movie night).
"""
from datetime import datetime, timezone
from pathlib import Path

from scrapers.base import RawEvent
from scrapers import elrio

FIX = Path(__file__).parent / "fixtures"
NGEVENT = (FIX / "elrio_ngevent.json").read_text()


def test_matches():
    assert elrio.matches("https://www.elriosf.com/")
    assert elrio.matches("https://www.elriosf.com/home#calendar")
    assert not elrio.matches("https://www.sfbarguide.com/")


def test_source_and_name():
    assert elrio.SOURCE == "elriosf.com"
    assert elrio.NAME == "El Rio"


def test_parse_events_returns_all():
    evs = elrio.parse_events(NGEVENT)
    assert len(evs) == 4
    assert all(isinstance(e, RawEvent) for e in evs)


def test_start_time_is_utc():
    ev = elrio.parse_events(NGEVENT)[0]  # Thots Not Cops, 2026-09-24 19:30 PDT
    assert ev.start_time.tzinfo == timezone.utc
    # 19:30 PDT (UTC-7) -> 02:30 UTC the next day.
    assert ev.start_time == datetime(2026, 9, 25, 2, 30, tzinfo=timezone.utc)


def test_title_is_the_summary():
    evs = elrio.parse_events(NGEVENT)
    titles = [e.title for e in evs]
    assert "Karaokiki with JoSie (FREE)" in titles
    assert titles[0].startswith("Thots Not Cops")


def test_url_points_at_tockify_detail_page():
    ev = elrio.parse_events(NGEVENT)[0]
    # detail/<uid>/<tid> — the info page, not a checkout link.
    assert ev.url == "https://tockify.com/elriosf2/detail/4333/1790303400000"


def test_location_is_el_rio_sf():
    ev = elrio.parse_events(NGEVENT)[0]
    assert ev.location and "El Rio" in ev.location
    assert "San Francisco" in ev.location


def test_description_is_cleaned_plaintext():
    ev = elrio.parse_events(NGEVENT)[0]
    assert ev.description
    # No HTML / no embedded media tag survives.
    assert "<" not in ev.description
    assert "tkfmedia" not in ev.description
    assert ev.description.startswith("We are proud to host THOTS NOT COPS at El Rio!")
    assert "$15 - $25 NOTAFLOF / 21+" in ev.description


def test_description_decodes_html_entities():
    salsa = next(e for e in elrio.parse_events(NGEVENT) if e.title.startswith("Salsa"))
    # &oacute; -> ó, and no raw entity/markup leaks through.
    assert "Tradición" in salsa.description
    assert "&oacute;" not in salsa.description


def test_image_url_uses_variant_format():
    # Salsa's master image is a png but the square variant is served as jpg
    # (variantFormat), so the URL must use the variant extension.
    salsa = next(e for e in elrio.parse_events(NGEVENT) if e.title.startswith("Salsa"))
    assert salsa.image_url == (
        "https://d3flpus5evl89n.cloudfront.net/"
        "5ecdc94bdf82fe44f68247b5/6ab183a2cd7aa68c2cab84da/square_272x272.jpg"
    )


def test_empty_and_malformed_are_safe():
    assert elrio.parse_events('{"events": []}') == []
    assert elrio.parse_events("not json") == []
    assert elrio.parse_events('{"metaData": {}}') == []


def test_scrape_uses_injected_fetcher():
    evs = elrio.scrape(fetch=lambda url: NGEVENT)
    assert len(evs) == 4
    assert any(e.title.startswith("Thots Not Cops") for e in evs)
