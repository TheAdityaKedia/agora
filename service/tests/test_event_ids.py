"""Stable event ids and aliases (feature-specs/event-lifecycle.md, §1)."""
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import dedupe_existing
import event_ids
import rekey_events
from main import save_events
from models import Base, Event, EventAlias
from scrapers.base import RawEvent

PT = ZoneInfo("America/Los_Angeles")
START = (datetime.now(timezone.utc) + timedelta(days=3)).replace(hour=3, minute=0, second=0, microsecond=0)


@contextmanager
def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    with patch("main.get_session", return_value=session):
        yield session
    session.close()


@pytest.fixture
def session():
    with _db() as s:
        yield s


def _raw(title="Jazz Night", url="https://a.com/e/1", start=START, location="Venue"):
    return RawEvent(title=title, start_time=start, location=location, url=url, description="d")


# --- the key rule -------------------------------------------------------------

def test_key_with_url_uses_url_and_utc_start_to_the_second():
    local = datetime(2026, 10, 9, 19, 30, 0, 123456, tzinfo=PT)
    assert event_ids.event_key("Blackbird", "https://b.com/x", "Ignored", local) \
        == "Blackbird|url|https://b.com/x|2026-10-10T02:30:00+00:00"


def test_key_without_url_uses_the_normalized_title():
    t = datetime(2026, 10, 10, 2, 30, tzinfo=timezone.utc)
    assert event_ids.event_key("Email", None, "  ¡Poetry   NIGHT!! ", t) \
        == "Email|title|poetry night|2026-10-10T02:30:00+00:00"
    # Same event however the source spells the edges or the spacing.
    assert event_ids.event_id("Email", None, "Poetry Night", t) \
        == event_ids.event_id("Email", None, "“poetry night.”", t)
    assert event_ids.event_id("Email", None, "Poetry Night", t) \
        != event_ids.event_id("Email", None, "Poetry Night II", t)


def test_normalize_title_keeps_non_latin_letters_and_inner_punctuation():
    assert event_ids.normalize_title("《茶馆》") == "茶馆"
    assert event_ids.normalize_title("Rock & Roll: Live!") == "rock & roll: live"
    assert event_ids.normalize_title("...") == ""


def test_ids_are_uuid5_and_depend_on_source_url_and_time():
    t = START
    a = event_ids.event_id("A", "https://x/1", "T", t)
    assert isinstance(a, uuid.UUID) and a.version == 5
    assert a == event_ids.event_id("A", "https://x/1", "Other title", t)  # url wins over title
    assert a != event_ids.event_id("B", "https://x/1", "T", t)
    assert a != event_ids.event_id("A", "https://x/2", "T", t)
    assert a != event_ids.event_id("A", "https://x/1", "T", t + timedelta(hours=1))


# --- saving -------------------------------------------------------------------

def test_new_rows_get_their_stable_id(session):
    save_events([_raw(), _raw(title="No link", url=None)], source="Blackbird")
    rows = {e.title: e for e in session.query(Event).all()}
    assert rows["Jazz Night"].id == event_ids.event_id("Blackbird", "https://a.com/e/1", "Jazz Night", START)
    assert rows["No link"].id == event_ids.event_id("Blackbird", None, "No link", START)


def test_wipe_and_rescrape_gives_the_same_ids():
    batch = {"First": [_raw(), _raw(title="Talk", url=None, start=START + timedelta(days=1))],
             "Second": [_raw(title="Jazz Night", url="https://agg.com/9"),   # merges into First's row
                        _raw(title="Only here", url="https://agg.com/10")]}

    def scrape():
        with _db() as s:
            for source in ("First", "Second"):  # sources.txt order
                save_events(batch[source], source=source)
            return {(e.title, tuple(e.sources)): e.id for e in s.query(Event).all()}

    first, second = scrape(), scrape()
    assert first == second and len(first) == 3
    assert first[("Jazz Night", ("First", "Second"))] == event_ids.event_id(
        "First", "https://a.com/e/1", "Jazz Night", START)


def test_a_taken_id_falls_back_to_a_random_one(session):
    save_events([_raw()], source="S")
    row = session.query(Event).one()
    row.url, row.title = "https://a.com/moved", "Renamed"  # no longer matches the scrape
    session.commit()
    save_events([_raw()], source="S")
    ids = [e.id for e in session.query(Event).all()]
    assert len(ids) == 2 and len(set(ids)) == 2


# --- aliases ------------------------------------------------------------------

def test_chains_resolve_with_a_cycle_guard():
    aliases = {"a": "b", "b": "c", "x": "y", "y": "x"}
    assert event_ids.resolve(aliases, "a") == "c"
    assert event_ids.resolve(aliases, "c") == "c"
    assert event_ids.resolve(aliases, "x") in {"x", "y"}  # terminates
    # A live row is its own answer, even if an old alias names it.
    assert event_ids.resolve(aliases, "a", live={"b"}) == "b"


def test_add_alias_replaces_and_ignores_self(session):
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    event_ids.add_alias(session, a, b, "merged")
    event_ids.add_alias(session, a, c, "moved")
    event_ids.add_alias(session, c, c, "merged")
    session.commit()
    assert event_ids.alias_map(session) == {str(a): str(c)}
    assert session.get(EventAlias, a).reason == "moved"
    with pytest.raises(ValueError):
        event_ids.add_alias(session, a, b, "typo")


def test_dedupe_existing_leaves_merged_aliases(session):
    keep = Event(title="Lindy West", start_time=START, location="Sydney Goldstein Theater", url=None,
                 sources=["City Arts & Lectures"], created_at=START)
    dup = Event(title="Offsite: Lindy West at City Arts & Lectures", start_time=START, location=None,
                url=None, sources=["Green Apple Books"], created_at=START)
    session.add_all([keep, dup]); session.commit()
    keep_id, dup_id = keep.id, dup.id
    p = dedupe_existing.plan(session, source_order=["City Arts & Lectures", "Green Apple Books"])
    dedupe_existing.apply(session, p)
    a = session.get(EventAlias, dup_id)
    assert (a.new_id, a.reason) == (keep_id, "merged")


# --- the one-off re-key -------------------------------------------------------

def _legacy(session, title, url, sources, start=START):
    """A row saved before stable ids: random id."""
    e = Event(id=uuid.uuid4(), title=title, start_time=start, location="V", url=url,
              sources=sources, created_at=START)
    session.add(e); session.commit()
    return e


def test_rekey_dry_run_changes_nothing_and_apply_records_aliases(session):
    old_a = _legacy(session, "A", "https://s.com/a", ["S", "T"]).id
    old_b = _legacy(session, "B", None, ["T"]).id
    save_events([_raw(title="C", url="https://s.com/c")], source="S")  # already stable
    p = rekey_events.plan(session)
    assert (len(p.rekey), p.unchanged, p.collisions) == (2, 1, [])
    assert {e.id for e in session.query(Event).all()} >= {old_a, old_b}  # dry run

    assert rekey_events.apply(session, p) == 2
    session.expire_all()
    new_a = event_ids.event_id("S", "https://s.com/a", "A", START)
    new_b = event_ids.event_id("T", None, "B", START)
    assert {e.title: e.id for e in session.query(Event).all()} == {
        "A": new_a, "B": new_b, "C": event_ids.event_id("S", "https://s.com/c", "C", START)}
    assert event_ids.alias_map(session) == {str(old_a): str(new_a), str(old_b): str(new_b)}
    assert {r.reason for r in session.query(EventAlias).all()} == {"rekeyed"}
    # Idempotent: a second run has nothing to do.
    assert rekey_events.plan(session).rekey == []


def test_rekey_skips_collisions(session):
    # The stable row exists, and a legacy row computes the same id: a missed
    # duplicate. Both are left alone.
    save_events([_raw(title="Show", url=None)], source="S")
    legacy = _legacy(session, "show!", None, ["S"]).id
    # Two legacy rows computing one id (same source, same URL-less title).
    x = _legacy(session, "Talk", None, ["T"], start=START + timedelta(days=1)).id
    y = _legacy(session, "talk!", None, ["T"], start=START + timedelta(days=1)).id
    p = rekey_events.plan(session)
    assert p.rekey == []
    assert {e.id for e, _, _ in p.collisions} == {legacy, x, y}
    rekey_events.apply(session, p)
    assert session.query(EventAlias).count() == 0


def test_rekey_shortens_existing_chains(session):
    a = _legacy(session, "A", "https://s.com/a", ["S"])
    gone = uuid.uuid4()
    event_ids.add_alias(session, gone, a.id, "merged")
    session.commit()
    old_a = a.id
    rekey_events.apply(session, rekey_events.plan(session))
    new_a = event_ids.event_id("S", "https://s.com/a", "A", START)
    assert event_ids.alias_map(session) == {str(gone): str(new_a), str(old_a): str(new_a)}
