import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from exporters.json_export import export_json
from models import Base, Event


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    with patch("exporters.json_export.get_session", return_value=session):
        yield session
    session.close()


def _make_event(title, when, url="https://example.com/x", sources=None):
    return Event(
        title=title,
        start_time=when,
        location="somewhere",
        url=url,
        description=None,
        sources=sources or ["test"],
        created_at=datetime.now(timezone.utc),
    )


def test_export_writes_manifest_with_metadata(db_session, tmp_path):
    future = datetime.now(timezone.utc) + timedelta(days=7)
    db_session.add(_make_event("Upcoming", future))
    db_session.commit()

    out = tmp_path / "events.json"
    count = export_json(out)

    assert count == 1
    data = json.loads(out.read_text())
    assert "generated_at" in data
    assert len(data["events"]) == 1
    assert data["events"][0]["title"] == "Upcoming"
    # Manifest emits `sources` as a JSON list.
    assert data["events"][0]["sources"] == ["test"]


def test_export_serializes_multi_source(db_session, tmp_path):
    future = datetime.now(timezone.utc) + timedelta(days=7)
    db_session.add(_make_event("Co-presented", future, sources=["atgtickets.com", "act-sf.org"]))
    db_session.commit()

    out = tmp_path / "events.json"
    export_json(out)
    payload = json.loads(out.read_text())["events"][0]
    assert payload["sources"] == ["atgtickets.com", "act-sf.org"]


def test_export_prunes_past_events(db_session, tmp_path):
    now = datetime.now(timezone.utc)
    db_session.add(_make_event("Long past", now - timedelta(days=30), url="https://example.com/past"))
    db_session.add(_make_event("Upcoming", now + timedelta(days=5), url="https://example.com/upcoming"))
    db_session.commit()

    out = tmp_path / "events.json"
    count = export_json(out)

    assert count == 1
    data = json.loads(out.read_text())
    assert [e["title"] for e in data["events"]] == ["Upcoming"]


def test_export_orders_by_start_time(db_session, tmp_path):
    now = datetime.now(timezone.utc)
    db_session.add(_make_event("Later", now + timedelta(days=20), url="https://example.com/later"))
    db_session.add(_make_event("Sooner", now + timedelta(days=2), url="https://example.com/sooner"))
    db_session.commit()

    out = tmp_path / "events.json"
    export_json(out)

    data = json.loads(out.read_text())
    assert [e["title"] for e in data["events"]] == ["Sooner", "Later"]


def test_export_respects_back_window(db_session, tmp_path):
    now = datetime.now(timezone.utc)
    # 3 days ago — outside default 1-day window (today only), inside 7-day window
    db_session.add(_make_event("Recent past", now - timedelta(days=3), url="https://example.com/recent"))
    db_session.commit()

    out = tmp_path / "events.json"
    assert export_json(out) == 0  # default 1-day window excludes it
    assert export_json(out, back_window_days=7) == 1  # 7-day window keeps it


def test_export_keeps_earlier_today_events(db_session, tmp_path):
    """An event that already started earlier today (local) must stay in the
    manifest — the back window is measured in calendar days in EXPORT_TZ, not
    rolling 24h, so today's 10am shows are still visible at 11pm today.
    """
    from zoneinfo import ZoneInfo

    pacific = ZoneInfo("America/Los_Angeles")
    # A datetime "today at 06:00 local time" — well past for anyone reading
    # this after mid-morning, but same calendar day → must be kept.
    today_local = datetime.now(pacific).date()
    six_am_today = datetime.combine(today_local, datetime.min.time().replace(hour=6), tzinfo=pacific)
    db_session.add(_make_event("Morning show today", six_am_today.astimezone(timezone.utc),
                               url="https://example.com/morning"))
    db_session.commit()

    out = tmp_path / "events.json"
    assert export_json(out) == 1  # today's earlier-today event still included


def test_export_drops_yesterday_events_with_default_window(db_session, tmp_path):
    """Yesterday's events (any hour) drop out of the default 1-day window,
    even a yesterday-11pm event that ended <25h ago.
    """
    from zoneinfo import ZoneInfo

    pacific = ZoneInfo("America/Los_Angeles")
    today_local = datetime.now(pacific).date()
    yesterday_local = today_local - timedelta(days=1)
    late_yesterday = datetime.combine(
        yesterday_local, datetime.min.time().replace(hour=23), tzinfo=pacific,
    )
    db_session.add(_make_event("Yesterday late", late_yesterday.astimezone(timezone.utc),
                               url="https://example.com/yesterday"))
    db_session.commit()

    out = tmp_path / "events.json"
    assert export_json(out) == 0
    assert export_json(out, back_window_days=2) == 1  # 2-day window keeps yesterday


def test_export_orders_same_time_events_by_id_for_stable_diffs(db_session, tmp_path):
    """Events sharing a start_time must be ordered by id, so re-exporting the
    same data yields a byte-identical file (minimal git diffs) instead of
    reshuffling rows by nondeterministic DB order.
    """
    import uuid

    when = datetime.now(timezone.utc) + timedelta(days=5)
    id_lo = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
    id_hi = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
    # Insert hi-id first so DB insertion order is the reverse of id order.
    e_hi = _make_event("Later-inserted", when, url="https://example.com/2")
    e_hi.id = id_hi
    e_lo = _make_event("Earlier-inserted", when, url="https://example.com/1")
    e_lo.id = id_lo
    db_session.add(e_hi)
    db_session.add(e_lo)
    db_session.commit()

    out = tmp_path / "events.json"
    export_json(out)
    ids = [e["id"] for e in json.load(open(out))["events"]]
    assert ids == [str(id_lo), str(id_hi)]


# --- classification join (tagging) ---------------------------------------

def _seed_classifications(tmp_path, entries):
    """Write a classifications.json and return its path."""
    import json as _json
    path = tmp_path / "classifications.json"
    path.write_text(_json.dumps({"entries": entries}))
    return path


def test_export_joins_classification_types_topics_cost(db_session, tmp_path):
    now = datetime.now(timezone.utc)
    db_session.add(_make_event("Branford Marsalis Quartet", now + timedelta(days=3),
                               sources=["SFJAZZ Center"]))
    db_session.commit()
    cpath = _seed_classifications(tmp_path, {
        "SFJAZZ Center\x1fBranford Marsalis Quartet": {
            "title": "Branford Marsalis Quartet", "source": "SFJAZZ Center",
            "types": [["performance"]], "topics": ["jazz"], "cost": "paid",
            "model": "m", "taxonomy_version": 1, "classified_at": "2026-09-23T00:00:00+00:00",
        }
    })
    out = tmp_path / "events.json"
    export_json(out, classifications_path=cpath)
    data = json.loads(out.read_text())
    ev = data["events"][0]
    assert ev["types"] == [["performance"]]
    assert ev["topics"] == ["jazz"]
    assert ev["cost"] == "paid"


def test_export_untagged_event_gets_empty_tags(db_session, tmp_path):
    now = datetime.now(timezone.utc)
    db_session.add(_make_event("Mystery Show", now + timedelta(days=3), sources=["Nowhere"]))
    db_session.commit()
    out = tmp_path / "events.json"
    export_json(out, classifications_path=tmp_path / "none.json")
    ev = json.loads(out.read_text())["events"][0]
    assert ev["types"] == []
    assert ev["topics"] == []
    assert ev["cost"] == "unknown"


def test_export_includes_taxonomy_block(db_session, tmp_path):
    now = datetime.now(timezone.utc)
    db_session.add(_make_event("X", now + timedelta(days=3)))
    db_session.commit()
    out = tmp_path / "events.json"
    export_json(out, classifications_path=tmp_path / "none.json")
    data = json.loads(out.read_text())
    assert "taxonomy" in data
    assert "type" in data["taxonomy"]["axes"]
    assert "topic" in data["taxonomy"]["axes"]
