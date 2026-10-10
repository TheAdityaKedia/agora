"""Updates, seen-tracking and explicit statuses in the save path
(feature-specs/event-lifecycle.md, §2)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db
import event_ids
import lifecycle
from main import save_events
from models import Base, Event
from scrapers.base import RawEvent

START = (datetime.now(timezone.utc) + timedelta(days=3)).replace(hour=3, minute=0, second=0, microsecond=0)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    with patch("main.get_session", return_value=s):
        yield s
    s.close()


def _raw(title="Jazz Night", url="https://a.com/e/1", location="The Venue", description="About it",
         image_url=None, status=None, start=START):
    return RawEvent(title=title, start_time=start, location=location, url=url,
                    description=description, image_url=image_url, status=status)


def _one(session) -> Event:
    session.expire_all()
    return session.query(Event).one()


def _save(raws, source):
    stats = {}
    counts = save_events(raws, source=source, stats=stats)
    return counts + (stats["updated"],)


# --- title markers ------------------------------------------------------------

@pytest.mark.parametrize("title, want", [
    ("CANCELLED: Jazz Night", ("Jazz Night", "cancelled")),
    ("[Canceled] Jazz Night", ("Jazz Night", "cancelled")),
    ("POSTPONED - Jazz Night", ("Jazz Night", "postponed")),
    ("Cancelled — Jazz Night", ("Jazz Night", "cancelled")),
    ("(Postponed) Jazz Night", ("Jazz Night", "postponed")),
    ("Jazz Night (CANCELLED)", ("Jazz Night", "cancelled")),
    ("Jazz Night - Postponed", ("Jazz Night", "postponed")),
    # Not markers: a show whose name starts with the word, or uses it mid-title.
    ("Cancelled Plans", ("Cancelled Plans", None)),
    ("Cancelled Plans: A Comedy Night", ("Cancelled Plans: A Comedy Night", None)),
    ("The Postponed Wedding", ("The Postponed Wedding", None)),
    ("Jazz Night", ("Jazz Night", None)),
])
def test_title_status(title, want):
    assert lifecycle.title_status(title) == want


def test_a_title_marker_keeps_the_id_and_matches_the_existing_row(session):
    _save([_raw(url=None)], "S")
    before = _one(session).id
    assert _save([_raw(title="CANCELLED: Jazz Night", url=None)], "S") == (0, 0, 0, 1)  # updated
    row = _one(session)
    assert (row.id, row.title, row.status) == (before, "Jazz Night", "cancelled")
    assert row.id == event_ids.event_id("S", None, "Jazz Night", START)


# --- inserts and seen-tracking ------------------------------------------------

def test_insert_records_seen_and_status(session):
    _save([_raw(), _raw(title="Off", url="https://a.com/e/2", status="cancelled")], "S")
    rows = {e.title: e for e in session.query(Event).all()}
    assert set(rows["Jazz Night"].seen) == {"S"} and rows["Jazz Night"].misses == {"S": 0}
    assert (rows["Jazz Night"].status, rows["Jazz Night"].status_at) == ("scheduled", None)
    assert rows["Off"].status == "cancelled" and rows["Off"].status_at is not None
    assert rows["Jazz Night"].changed is None and rows["Jazz Night"].changed_at is None


def test_every_match_marks_seen_and_resets_misses(session):
    _save([_raw()], "S")
    row = _one(session)
    row.misses = {"S": 1}
    row.seen = {"S": "2020-01-01T00:00:00+00:00"}
    session.commit()
    _save([_raw()], "S")                                     # same source
    _save([_raw(url="https://agg.com/9")], "Aggregator")     # a merge
    row = _one(session)
    assert row.misses == {"S": 0, "Aggregator": 0}
    assert set(row.seen) == {"S", "Aggregator"} and row.seen["S"] > "2020"


@pytest.mark.parametrize("gone", ["unlisted", "moved"])
def test_an_unlisted_row_listed_again_is_back(session, gone):
    _save([_raw()], "S")
    row = _one(session)
    row.status = gone
    session.commit()
    _save([_raw()], "S")
    row = _one(session)
    assert row.status == "scheduled" and row.status_at is not None


# --- same-source updates ------------------------------------------------------

def test_the_creating_source_updates_changed_fields(session):
    _save([_raw()], "S")
    counts = _save([_raw(location="The New Venue", description="New text",
                         image_url="https://img/1.jpg")], "S")
    assert counts == (0, 0, 0, 1)  # (saved, merged, skipped, updated)
    row = _one(session)
    assert (row.location, row.description, row.image_url) == ("The New Venue", "New text", "https://img/1.jpg")
    assert row.changed == {"location": "The Venue", "description": "About it", "image_url": None}
    assert row.changed_at is not None


def test_a_title_change_is_recorded_and_the_id_kept(session):
    _save([_raw()], "S")
    before = _one(session).id
    _save([_raw(title="Jazz Night: Live")], "S")   # matched on url + start
    row = _one(session)
    assert (row.id, row.title, row.changed) == (before, "Jazz Night: Live", {"title": "Jazz Night"})


def test_nothing_new_is_a_skip_and_empty_equals_none(session):
    _save([_raw(description="")], "S")
    assert _save([_raw(description=None)], "S") == (0, 0, 1, 0)
    row = _one(session)
    assert row.changed is None and row.changed_at is None


def test_other_sources_never_overwrite(session):
    _save([_raw()], "Venue")
    _save([_raw(url="https://agg.com/9", description="Aggregator blurb")], "Aggregator")  # merge
    # The aggregator, now on the row, re-lists it with other details: seen only.
    assert _save([_raw(url="https://agg.com/9", location="Elsewhere", description="Blurb 2")],
                 "Aggregator") == (0, 0, 1, 0)
    row = _one(session)
    assert (row.location, row.description, row.url) == ("The Venue", "About it", "https://a.com/e/1")
    assert row.changed is None and row.sources == ["Venue", "Aggregator"]


# --- explicit status ----------------------------------------------------------

def test_explicit_status_applies_and_relisting_without_it_uncancels(session):
    _save([_raw()], "S")
    _save([_raw(status="cancelled")], "S")
    row = _one(session)
    cancelled_at = row.status_at
    assert row.status == "cancelled" and cancelled_at is not None
    _save([_raw(status="cancelled")], "S")           # still cancelled: no new status_at
    assert _one(session).status_at == cancelled_at
    _save([_raw(status="postponed")], "S")
    assert _one(session).status == "postponed"
    _save([_raw()], "S")                             # listed again, no flag: on again
    assert _one(session).status == "scheduled"


def test_only_the_creating_source_sets_the_status(session):
    _save([_raw()], "Venue")
    _save([_raw(url="https://agg.com/9", status="cancelled")], "Aggregator")  # merge: ignored
    _save([_raw(url="https://agg.com/9", status="cancelled")], "Aggregator")  # re-list: ignored
    assert _one(session).status == "scheduled"


# --- schema -------------------------------------------------------------------

def test_postgres_migration_adds_every_lifecycle_column():
    sql = db.column_migrations("postgresql")
    added = {s.split("IF NOT EXISTS ")[1].split()[0] for s in sql}
    assert added == {"status", "status_at", "changed_at", "changed", "seen", "misses"}
    assert added <= set(Event.__table__.columns.keys())
    assert all(s.startswith("ALTER TABLE events ADD COLUMN IF NOT EXISTS ") for s in sql)
    assert db.column_migrations("sqlite") == []


def test_rawevent_status_round_trips_and_old_result_files_still_load():
    raw = _raw(status="postponed")
    assert RawEvent.from_dict(raw.to_dict()) == raw
    old = raw.to_dict()
    del old["status"]  # a result file written before the field existed
    assert RawEvent.from_dict(old).status is None
