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


def _make_event(title, when, url="https://example.com/x"):
    return Event(
        title=title,
        start_time=when,
        location="somewhere",
        url=url,
        description=None,
        source="test",
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
    # 3 days ago — within a 7-day back window, outside the default 1-day window
    db_session.add(_make_event("Recent past", now - timedelta(days=3), url="https://example.com/recent"))
    db_session.commit()

    out = tmp_path / "events.json"
    assert export_json(out) == 0  # default 1-day window excludes it
    assert export_json(out, back_window_days=7) == 1  # 7-day window keeps it
