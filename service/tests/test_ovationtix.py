import json
from datetime import datetime, timezone
from pathlib import Path

from scrapers import henryj, oaklandtheaterproject, ovationtix

FIXTURES = Path(__file__).parent / "fixtures"
ADDRESS = henryj.ADDRESS


def _load():
    return (json.loads((FIXTURES / "ovationtix_calendar.json").read_text()),
            json.loads((FIXTURES / "ovationtix_productions.json").read_text()))


def test_parse_joins_calendar_and_catalog():
    calendar, productions = _load()
    events = ovationtix.parse_events(calendar, productions, client_id="36995",
                                     fallback_location=ADDRESS)
    finale = events[0]
    assert finale.title.startswith("Oakland Tech Week Finale")
    assert finale.start_time == datetime(2026, 10, 4, 1, 30, tzinfo=timezone.utc)  # 6:30pm PDT
    assert finale.location == f"Calvin Simmons Theater, {ADDRESS}"
    assert finale.url == "https://ci.ovationtix.com/36995/production/1291869"
    assert finale.image_url.startswith(ovationtix.API_BASE + "/ClientFile(")
    assert finale.description


def test_timed_entry_slots_collapse_to_earliest():
    calendar, productions = _load()
    events = ovationtix.parse_events(calendar, productions, client_id="36995",
                                     fallback_location=ADDRESS)
    banksy = [e for e in events if e.title == "The Banksy Art Exhibit"]
    assert len(banksy) == 1  # 8 slots that day → one listing
    day = next(d for d in calendar if any(p["name"] == "The Banksy Art Exhibit" for p in d["productions"]))
    slots = [ovationtix._parse_start(st["performanceStartTime"])
             for p in day["productions"] for st in p["showtimes"]]
    assert banksy[0].start_time == min(slots)
    assert len(events) == 4


def test_skips_cancelled_hidden_and_test_events():
    calendar = [{"date": "2026-10-10", "productions": [
        {"productionId": 1, "name": "Dream Warrior Test Event", "showtimes": [
            {"performanceStartTime": "2026-10-10 19:00"}]},
        {"productionId": 2, "name": "Real Show", "showtimes": [
            {"performanceStartTime": "2026-10-10 19:00", "isCancelled": True},
            {"performanceStartTime": "2026-10-10 20:00", "isVisible": False},
            {"performanceStartTime": "2026-10-10 21:00"}]},
    ]}]
    events = ovationtix.parse_events(calendar, [], client_id="1", fallback_location="X")
    assert [(e.title, e.start_time.hour) for e in events] == [("Real Show", 4)]
    assert events[0].location == "X"


def test_wrappers():
    assert oaklandtheaterproject.matches("https://oaklandtheaterproject.org/")
    assert henryj.matches("https://www.thehenryj.org/upcoming-events")
    assert not henryj.matches("https://www.zspace.org/")


def test_synopsis_cuts_at_first_logistics_label():
    text = ("World Premiere She Se Puede (a chorus of Huertas) by Lisa Ramirez directed by Karina Gutiérrez "
            "Dates : Nov 20–Dec 6 Times: Thu–Sat @ 7:30 p.m. Run time: 90 minutes Seating • General Admission")
    assert ovationtix._synopsis(text) == (
        "World Premiere She Se Puede (a chorus of Huertas) by Lisa Ramirez directed by Karina Gutiérrez")
    # A label at the very start leaves nothing worth keeping: text stays as is.
    assert ovationtix._synopsis("Dates: Oct 7. A watch party.") == "Dates: Oct 7. A watch party."
    assert ovationtix._synopsis("A concert with no logistics.") == "A concert with no logistics."
    assert ovationtix._synopsis(None) is None
