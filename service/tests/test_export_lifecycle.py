"""The manifest and event-index.json carry the lifecycle
(feature-specs/event-lifecycle.md, §4)."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import event_ids
from exporters.json_export import export_json, recent_change
from models import Base, Event

NOW = datetime.now(timezone.utc)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    with patch("exporters.json_export.get_session", return_value=session):
        yield session
    session.close()


def _add(session, title, days=3, status=None, **kw):
    e = Event(id=kw.pop("id", uuid.uuid4()), title=title, start_time=NOW + timedelta(days=days),
              location=kw.pop("location", "The Venue"), url=kw.pop("url", f"https://x.com/{title}"),
              description=kw.pop("description", "About it. More."), sources=kw.pop("sources", ["S"]),
              created_at=NOW, status=status, **kw)
    session.add(e)
    session.commit()
    return str(e.id)


def _export(tmp_path):
    out = tmp_path / "events.json"
    export_json(out)
    return json.loads(out.read_text()), json.loads((tmp_path / "event-index.json").read_text())


def test_cancelled_and_postponed_are_listed_with_status_gone_rows_are_not(db_session, tmp_path):
    ids = {t: _add(db_session, t, status=s) for t, s in [
        ("On", None), ("Also on", "scheduled"), ("Off", "cancelled"), ("Later", "postponed"),
        ("Unlisted", "unlisted"), ("Moved", "moved")]}
    manifest, index = _export(tmp_path)
    by_title = {e["title"]: e for e in manifest["events"]}
    assert set(by_title) == {"On", "Also on", "Off", "Later"}
    assert "status" not in by_title["On"] and "status" not in by_title["Also on"]
    assert (by_title["Off"]["status"], by_title["Later"]["status"]) == ("cancelled", "postponed")
    assert set(index["events"]) == {ids[t] for t in by_title}
    assert set(index["gone"]) == {ids["Unlisted"], ids["Moved"]}
    assert index["events"][ids["Off"]]["status"] == "cancelled"


def test_changes_show_for_seven_days_and_only_shown_fields(db_session, tmp_path):
    fresh = _add(db_session, "Fresh", changed_at=NOW - timedelta(days=2),
                 changed={"location": "Old Venue", "description": "old text", "start_time": "x"})
    _add(db_session, "Stale", changed_at=NOW - timedelta(days=8), changed={"location": "Old Venue"})
    _add(db_session, "Quiet", changed_at=NOW - timedelta(days=1), changed={"description": "old"})
    manifest, index = _export(tmp_path)
    by_title = {e["title"]: e for e in manifest["events"]}
    assert by_title["Fresh"]["changed"]["was"] == {"location": "Old Venue", "start_time": "x"}
    assert datetime.fromisoformat(by_title["Fresh"]["changed"]["at"]) <= NOW
    assert "changed" not in by_title["Stale"] and "changed" not in by_title["Quiet"]
    assert index["events"][fresh]["changed"] == by_title["Fresh"]["changed"]


def test_a_reworded_location_is_not_a_venue_change(db_session, tmp_path):
    _add(db_session, "Lawn", location="Great Lawn, Yerba Buena Gardens, 750 Howard St, San Francisco",
         changed_at=NOW, changed={"location": "Great Lawn, Yerba Buena Gardens, Mission St. between 3rd & 4th"})
    _add(db_session, "Both", location="540 Laguna St, San Francisco, CA",
         changed_at=NOW, changed={"location": "540 Laguna St + 540 Cafe", "title": "Old"})
    _add(db_session, "Moved", location="The Lost Church, 988 Columbus Ave",
         changed_at=NOW, changed={"location": "Bird & Beckett, 653 Chenery St"})
    manifest, index = _export(tmp_path)
    by_title = {e["title"]: e for e in manifest["events"]}
    assert "changed" not in by_title["Lawn"]
    assert by_title["Both"]["changed"]["was"] == {"title": "Old"}
    assert by_title["Moved"]["changed"]["was"] == {"location": "Bird & Beckett, 653 Chenery St"}


def test_recent_change_handles_naive_sqlite_times():
    e = Event(changed={"title": "Old"}, changed_at=(NOW - timedelta(hours=1)).replace(tzinfo=None))
    assert recent_change(e, now=NOW)["was"] == {"title": "Old"}


def test_index_events_carry_current_facts_without_descriptions(db_session, tmp_path):
    eid = _add(db_session, "Show", image_url="https://img/1.jpg", sources=["S", "T"])
    _add(db_session, "Plain", url=None)
    _, index = _export(tmp_path)
    assert index["events"][eid] == {
        "title": "Show", "start_time": index["events"][eid]["start_time"], "location": "The Venue",
        "url": "https://x.com/Show", "image_url": "https://img/1.jpg", "sources": ["S", "T"]}
    assert all("description" not in e and "summary" not in e for e in index["events"].values())
    assert "url" not in next(e for e in index["events"].values() if e["title"] == "Plain")


def test_gone_entries_and_moved_to(db_session, tmp_path):
    at = NOW - timedelta(hours=5)
    old = _add(db_session, "Gig", days=2, status="moved", status_at=at)
    new = _add(db_session, "Gig", days=4, url="https://x.com/Gig2",
               changed={"start_time": "2026-01-01T00:00:00+00:00"}, changed_at=NOW)
    unlisted = _add(db_session, "Gone", status="unlisted", status_at=at)
    event_ids.add_alias(db_session, uuid.UUID(old), uuid.UUID(new), "moved")
    db_session.commit()
    _, index = _export(tmp_path)
    assert index["gone"][old] == {"status": "moved", "at": at.isoformat(), "title": "Gig",
                                  "start_time": index["gone"][old]["start_time"], "moved_to": new}
    assert index["gone"][unlisted]["status"] == "unlisted" and "moved_to" not in index["gone"][unlisted]
    # The moved row is a row: it answers through gone, not through aliases.
    assert old not in index["aliases"]


def test_aliases_resolve_chains_and_only_lead_to_exported_events(db_session, tmp_path):
    cur = _add(db_session, "Current")
    past = _add(db_session, "Past", days=-3)
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    event_ids.add_alias(db_session, a, b, "merged")             # a → b → current
    event_ids.add_alias(db_session, b, uuid.UUID(cur), "rekeyed")
    event_ids.add_alias(db_session, c, uuid.UUID(past), "rekeyed")  # leads to a past event
    event_ids.add_alias(db_session, d, uuid.uuid4(), "merged")      # leads nowhere
    db_session.commit()
    manifest, index = _export(tmp_path)
    assert index["aliases"] == {str(a): cur, str(b): cur}
    assert "aliases" not in manifest  # the page gets ids from the collections API


def test_index_is_one_entry_per_line_and_regenerates_identically(db_session, tmp_path):
    for t in ("A", "B", "C"):
        _add(db_session, t)
    _export(tmp_path)
    text = (tmp_path / "event-index.json").read_text()
    event_lines = [l for l in text.splitlines() if l.startswith('"') and ': {"title"' in l]
    assert len(event_lines) == 3
    first = text.split("\n", 1)[1]
    _export(tmp_path)
    assert (tmp_path / "event-index.json").read_text().split("\n", 1)[1] == first  # past generated_at


def test_an_empty_database_writes_an_empty_index(db_session, tmp_path):
    _, index = _export(tmp_path)
    assert (index["events"], index["gone"], index["aliases"]) == ({}, {}, {})
    assert "generated_at" in index
