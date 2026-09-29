from datetime import datetime, timezone

from ingest import validate
from scrapers.base import RawEvent

NOW = datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc)  # Tue 11am PT


def cand(**kw):
    base = {"title": "Verses & Vinyl", "date": "2026-10-16", "start_time": "19:30", "end_time": None,
            "venue": "Borderlands Café", "address": "870 Valencia St, San Francisco",
            "description": "Open mic + DJ.", "cost_text": "$10 suggested", "url": None, "recurrence": None}
    base.update(kw)
    return base


def test_single_event_localized_to_utc_with_location_and_cost():
    events, reason = validate.candidate_to_events(cand(), now=NOW, url="https://x.example.com/e")
    assert reason is None and len(events) == 1
    e = events[0]
    assert e.start_time == datetime(2026, 10, 17, 2, 30, tzinfo=timezone.utc)  # 7:30pm PDT
    assert e.location == "Borderlands Café, 870 Valencia St, San Francisco"
    assert e.url == "https://x.example.com/e"
    assert e.description == "Open mic + DJ.\n\nCost: $10 suggested"


def test_required_fields():
    assert validate.candidate_to_events(cand(date=None), now=NOW) == ([], "couldn't find a date")
    assert validate.candidate_to_events(cand(start_time=None), now=NOW) == ([], "couldn't find a start time")


def test_past_and_non_bay_area_rejected_missing_location_allowed():
    assert validate.candidate_to_events(cand(date="2026-09-01"), now=NOW)[1] == "this event already happened"
    assert validate.candidate_to_events(cand(address="1 Main St, Los Angeles", venue=None),
                                        now=NOW)[1] == "not in the Bay Area"
    events, reason = validate.candidate_to_events(cand(venue=None, address=None), now=NOW)
    assert reason is None and events[0].location is None


def test_weekly_recurrence_expands_eight_weeks_or_until():
    rec = {"weekday": "thursday", "time": "19:00", "until": None}
    events, _ = validate.candidate_to_events(cand(date=None, start_time=None, recurrence=rec), now=NOW)
    assert len(events) == 8 and events[0].start_time == datetime(2026, 10, 2, 2, 0, tzinfo=timezone.utc)
    rec["until"] = "2026-10-15"
    events, _ = validate.candidate_to_events(cand(date=None, start_time=None, recurrence=rec), now=NOW)
    assert len(events) == 3  # Oct 1, 8 and 15 (local) — until is inclusive


def test_description_capped():
    events, _ = validate.candidate_to_events(cand(description="x" * 5000, cost_text=None), now=NOW)
    assert len(events[0].description) == 2000


def test_check_event_for_structured_results():
    ok = RawEvent("T", datetime(2026, 10, 3, 18, tzinfo=timezone.utc), "SF, CA", "u", None)
    assert validate.check_event(ok, now=NOW) is None
    past = RawEvent("T", datetime(2026, 9, 1, 18, tzinfo=timezone.utc), None, "u", None)
    assert validate.check_event(past, now=NOW) == "this event already happened"
    la = RawEvent("T", datetime(2026, 10, 3, 18, tzinfo=timezone.utc), "Los Angeles, CA", "u", None)
    assert validate.check_event(la, now=NOW) == "not in the Bay Area"


def test_venue_only_location_is_allowed_but_named_elsewhere_is_rejected():
    """'Dolores Park' names no city, so it can't fail the Bay Area check; an
    address that names a place (comma / state / ZIP) must be Bay Area."""
    events, reason = validate.candidate_to_events(cand(venue="Dolores Park", address=None), now=NOW)
    assert reason is None and events[0].location == "Dolores Park"
    assert validate.candidate_to_events(cand(venue="Mucky Duck", address="1315 9th Ave SF"),
                                        now=NOW)[0][0].location == "Mucky Duck, 1315 9th Ave SF"
    assert validate.candidate_to_events(cand(venue=None, address="200 Main St, Austin, TX 78701"),
                                        now=NOW)[1] == "not in the Bay Area"
