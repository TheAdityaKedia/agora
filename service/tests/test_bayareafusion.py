"""Bay Area Fusion Calendar: the app's occurrence rule, free-text times, and
expansion of real schedule rows (trimmed snapshot of its Supabase table)."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

from scrapers import bayareafusion as bf

ROWS = json.loads((Path(__file__).parent / "fixtures" / "bayareafusion_events.json").read_text())


def row(title):
    return next(r for r in ROWS if r["title"] == title)


def test_happens_on_matches_the_apps_rule():
    ebf = row("East Bay Fusion")              # every Tuesday
    assert bf.happens_on(ebf, "2026-10-13") and bf.happens_on(ebf, "2026-10-27")
    assert not bf.happens_on(ebf, "2026-10-14")
    assert not bf.happens_on(ebf, "2024-12-31")  # before the row existed
    mf = row("Mission Fusion")                # 1st and 3rd Saturdays, with skips
    assert bf.happens_on(mf, "2026-10-03") and bf.happens_on(mf, "2026-10-17")
    assert not bf.happens_on(mf, "2026-10-10")
    assert not bf.happens_on(mf, "2026-04-04")  # in skip_dates
    alchemy = row("Alchemy of Fusion")        # Fridays, but only its listed dates
    assert bf.happens_on(alchemy, "2026-10-16") and not bf.happens_on(alchemy, "2026-10-23")
    big = row("Big BAmF")                     # occasional: only_dates
    assert bf.happens_on(big, "2026-10-09") and not bf.happens_on(big, "2026-10-12")


def test_parse_start_time_free_text():
    assert bf.parse_start_time("7:00 PM - 11:30 PM") == (19, 0)
    assert bf.parse_start_time("6:00 - 11:45pm") == (18, 0)
    assert bf.parse_start_time("8pm - 2am") == (20, 0)
    assert bf.parse_start_time("9:15pm - 12am") == (21, 15)
    assert bf.parse_start_time("9:30 am - 10:30 pm") == (9, 30)
    assert bf.parse_start_time("Fri 8:00pm - Mon 5:00am") == (20, 0)
    assert bf.parse_start_time("9 - 2am") == (21, 0)
    assert bf.parse_start_time("9:30 - ") == (21, 30)
    assert bf.parse_start_time(" - ") == (12, 0) and bf.parse_start_time("") == (12, 0)


def test_expand_applies_overrides_skips_and_region():
    events = bf.expand(ROWS, date(2026, 2, 1), 140)  # Feb 1 – Jun 20, 2026
    titles = {e.title for e in events}
    assert "Firehouse 5" not in titles                         # Sacramento: outside the Bay Area
    assert "Alchemy of Fusion: Urban Kiz Connection Games" in titles  # per-date title override
    feb20 = next(e for e in events if e.title.startswith("Alchemy of Fusion: Urban"))
    assert feb20.start_time == datetime(2026, 2, 21, 3, 30, tzinfo=timezone.utc)  # 7:30 PM PST
    assert feb20.url.endswith("?date=2026-02-20") and feb20.location.endswith("Berkeley, CA")
    cuddles = next(e for e in events if e.title == "Cuddles'n'Blues")  # no city, East Bay region
    assert cuddles.location.endswith(", CA")
    mission = [e for e in events if e.title.startswith("Mission Fusion")]
    assert all(e.start_time.date() != date(2026, 4, 5) for e in mission)  # Apr 4 skipped
    solar = [e for e in events if e.url.endswith("?date=2026-02-12")]
    assert solar and solar[0].title == "Solar Fusion- MACRO/micro night!"
    assert all(e.image_url for e in events)


def test_matches():
    assert bf.matches("https://bayareafusioncal.com/")
    assert not bf.matches("https://www.facebook.com/MissionFusion/events")
