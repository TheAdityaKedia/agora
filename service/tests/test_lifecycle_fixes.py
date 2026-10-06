"""Regressions found reviewing the event-lifecycle branch.

Each test fails on the state of the branch before the fix beside it:
  - a change to an unshown field erased a live badge
  - two same-title events from one source made a row flap forever
  - a cancelled timed-entry slot cancelled the whole open day
  - re-keying churned ids that were already stable
  - a `moved` row came back as scheduled, listing a showing twice
  - a possibly-partial scrape never alerted
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import ci
import event_ids
import lifecycle
import rekey_events
from exporters.json_export import recent_change
from main import save_events
from models import Base, Event
from scrapers.base import RawEvent

START = (datetime.now(timezone.utc) + timedelta(days=3)).replace(hour=3, minute=0, second=0, microsecond=0)
NOW = datetime.now(timezone.utc)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    with patch("main.get_session", return_value=s):
        yield s
    s.close()


def _raw(title="Jazz Night", url="https://a.com/e/1", location="The Venue",
         description="About it", image_url=None, status=None, start=START):
    return RawEvent(title=title, start_time=start, location=location, url=url,
                    description=description, image_url=image_url, status=status)


# --- a change to an unshown field must not erase a live badge ---------------

def test_description_update_keeps_a_live_reschedule_badge():
    """A reschedule records changed.start_time; a later description-only
    re-scrape must not wipe it, or the badge dies before its 7 days."""
    was_start = (START - timedelta(hours=1)).isoformat()
    row = Event(title="Jazz Night", start_time=START, location="The Venue",
                url="https://a.com/e/1", description="About it", sources=["A"],
                changed={"start_time": was_start}, changed_at=NOW - timedelta(days=1))

    assert lifecycle.apply_update(row, _raw(description="New blurb"), NOW) is True

    assert row.changed["start_time"] == was_start      # survives
    assert row.changed["description"] == "About it"    # and the new one is recorded
    assert recent_change(row, now=NOW)["was"] == {"start_time": was_start}


def test_changed_keeps_the_oldest_value_within_the_window():
    """Two changes to one shown field: the badge shows the value from before
    the first, not the intermediate one."""
    row = Event(title="First", start_time=START, sources=["A"],
                changed={"title": "Original"}, changed_at=NOW - timedelta(days=2))
    lifecycle.apply_update(row, _raw(title="Second", description=None, location=None), NOW)
    assert row.changed["title"] == "Original"


def test_expired_changes_are_dropped_not_carried_forever():
    row = Event(title="Jazz Night", start_time=START, location="The Venue",
                url="https://a.com/e/1", description="About it", sources=["A"],
                changed={"start_time": (START - timedelta(hours=1)).isoformat()},
                changed_at=NOW - timedelta(days=lifecycle.CHANGED_DAYS + 1))
    lifecycle.apply_update(row, _raw(description="New blurb"), NOW)
    assert "start_time" not in row.changed


# --- one source, two events with the same title at the same time ------------

def test_two_same_title_events_from_one_source_do_not_flap_the_row(session):
    """Screen 1 and Screen 2 at 19:30. The title rule matches both to one row;
    the second must not update over the first, in this run or the next."""
    a = _raw(url="https://a.com/screen-1", location="Screen 1")
    b = _raw(url="https://a.com/screen-2", location="Screen 2")

    saved, merged, skipped = save_events([a, b], source="A")
    assert (saved, merged, skipped) == (1, 0, 1)

    session.expire_all()
    row = session.query(Event).one()
    assert (row.location, row.url) == ("Screen 1", "https://a.com/screen-1")
    assert row.changed in (None, {})          # no bogus "Venue changed"

    stats = {}
    save_events([a, b], source="A", stats=stats)   # the next run
    session.expire_all()
    row = session.query(Event).one()
    assert stats["updated"] == 0               # nothing to update: no churn
    assert (row.location, row.url) == ("Screen 1", "https://a.com/screen-1")
    assert row.changed in (None, {})


def test_order_in_the_batch_does_not_matter(session):
    """Whichever comes first wins, and the other is skipped — not an update."""
    b = _raw(url="https://a.com/screen-2", location="Screen 2")
    a = _raw(url="https://a.com/screen-1", location="Screen 1")
    stats = {}
    save_events([b, a], source="A", stats=stats)
    session.expire_all()
    assert session.query(Event).one().location == "Screen 2"
    assert stats["updated"] == 0


# --- a cancelled timed-entry slot --------------------------------------------

def _rows(hours, cancelled=(), pid=7):
    return [(pid, "2026-11-03", RawEvent(
        title="Exhibition", start_time=datetime(2026, 11, 3, h, tzinfo=timezone.utc),
        location="The Museum", url="https://t.com/p/7", description=None, image_url=None,
        status="cancelled" if h in cancelled else None)) for h in hours]


def test_one_cancelled_slot_does_not_cancel_the_open_day():
    from scrapers import ovationtix
    rows = _rows((10, 11, 12, 13, 14, 15), cancelled=(10,))
    with patch.object(ovationtix, "TIMED_ENTRY_PER_DAY", 3):
        events = ovationtix.collapse_timed_entry(rows)
    assert len(events) == 1
    assert events[0].status is None
    assert events[0].start_time.hour == 11


def test_a_fully_cancelled_timed_day_still_reports_cancelled():
    """No open slot left: the production is represented by a cancelled one."""
    from scrapers import ovationtix
    rows = _rows((10, 11, 12, 13), cancelled=(10, 11, 12, 13))
    with patch.object(ovationtix, "TIMED_ENTRY_PER_DAY", 3):
        events = ovationtix.collapse_timed_entry(rows)
    assert [e.status for e in events] == ["cancelled"]


# --- re-keying must not churn stable ids -------------------------------------

def test_rekey_leaves_a_derived_id_alone_after_its_url_changed(session):
    """The id was computed at creation; an update later corrected the url. The
    row is still stable — re-keying it would break collections holding it."""
    created = _raw(url="https://a.com/e/1")
    eid = event_ids.event_id("A", created.url, created.title, created.start_time)
    session.add(Event(id=eid, title=created.title, start_time=START, location="The Venue",
                      url="https://a.com/e/1?utm=x", description=None, sources=["A"],
                      created_at=NOW))
    session.commit()

    plan = rekey_events.plan(session)
    assert (len(plan.rekey), plan.unchanged) == (0, 1)


def test_rekey_still_fixes_a_random_id(session):
    session.add(Event(id=uuid.uuid4(), title="Jazz Night", start_time=START,
                      location="The Venue", url="https://a.com/e/1", sources=["A"],
                      created_at=NOW))
    session.commit()
    plan = rekey_events.plan(session)
    assert len(plan.rekey) == 1


# --- a moved row must stay moved ---------------------------------------------

def test_a_moved_row_listed_again_has_its_move_undone(session):
    """The old showing is back: it is scheduled again, the alias that pointed
    this id at the successor is gone, and the successor no longer claims it was
    rescheduled from this time. Both showings then stand on their own."""
    from models import EventAlias
    old = Event(id=uuid.uuid4(), title="Jazz Night", start_time=START, location="The Venue",
                url="https://a.com/e/1", description="About it", sources=["A"], created_at=NOW,
                status=lifecycle.MOVED, status_at=NOW - timedelta(days=1))
    new = Event(id=uuid.uuid4(), title="Jazz Night", start_time=START + timedelta(days=1),
                location="The Venue", url="https://a.com/e/1", sources=["A"], created_at=NOW,
                changed={"start_time": START.isoformat()}, changed_at=NOW - timedelta(days=1))
    session.add_all([old, new])
    session.add(EventAlias(old_id=old.id, new_id=new.id, reason=lifecycle.MOVED, at=NOW))
    session.commit()
    old_id, new_id = old.id, new.id   # save_events closes the session: detaches rows

    save_events([_raw()], source="A")

    assert session.get(Event, old_id).status == lifecycle.SCHEDULED
    assert session.get(EventAlias, old_id) is None            # no stale redirect
    assert session.get(Event, new_id).changed in (None, {})   # no stale "was …"


def test_an_unrelated_alias_is_left_alone(session):
    """Only a `moved` alias is undone — a merged/rekeyed one must survive."""
    from models import EventAlias
    row = Event(id=uuid.uuid4(), title="Jazz Night", start_time=START, location="The Venue",
                url="https://a.com/e/1", description="About it", sources=["A"], created_at=NOW,
                status=lifecycle.MOVED, status_at=NOW - timedelta(days=1))
    other = uuid.uuid4()
    session.add(row)
    session.add(EventAlias(old_id=row.id, new_id=other, reason="merged", at=NOW))
    session.commit()
    row_id = row.id
    save_events([_raw()], source="A")
    assert session.get(EventAlias, row_id) is not None


def test_mark_seen_still_restores_an_unlisted_row():
    row = Event(title="Jazz Night", start_time=START, sources=["A"],
                status=lifecycle.UNLISTED, status_at=NOW - timedelta(days=1))
    lifecycle.mark_seen(row, "A", NOW)
    assert row.status == lifecycle.SCHEDULED


# --- a possibly-partial scrape alerts ----------------------------------------

def _row(**kw):
    base = {"url": "https://a.com/", "name": "A", "status": "ok", "events": 40,
            "saved": 0, "merged": 0, "updated": 0, "skipped": 40, "error": None}
    return {**base, **kw}


def test_possibly_partial_source_alerts():
    alerts = ci.find_alerts({"sources": [_row(possibly_partial=True)]}, local_only=[])
    assert len(alerts) == 1
    assert "possibly partial" in alerts[0]["reason"]
    body = ci.render_alert_body(alerts, "https://run", "@owner")
    assert "possibly partial" in body


def test_a_healthy_source_still_does_not_alert():
    assert ci.find_alerts({"sources": [_row(possibly_partial=False)]}, local_only=[]) == []


def test_local_only_sources_are_still_exempt_when_partial():
    rows = [_row(url="https://greenapplebooks.com/events", possibly_partial=True)]
    assert ci.find_alerts({"sources": rows}, local_only=["greenapplebooks.com"]) == []
