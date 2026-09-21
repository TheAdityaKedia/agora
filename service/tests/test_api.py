from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api import app
from models import Base, Event


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    with patch("api.get_session", return_value=session):
        yield session
    session.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_event(db_session):
    event = Event(
        title="Test Event",
        start_time=datetime(2026, 6, 1, 19, 0),
        location="1231 9th Ave, San Francisco",
        url="https://greenapplebooks.com/event/test",
        description="A great event.",
        sources=["greenapplebooks.com"],
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)
    return event


@pytest.fixture
def multi_source_event(db_session):
    """Event listed by two sources (like an ATG+A.C.T. co-presentation)."""
    event = Event(
        title="Oh, Mary!",
        start_time=datetime(2026, 10, 13, 19, 0),
        location="Curran Theatre",
        url="https://us.atgtickets.com/events/oh-mary/curran-theater/",
        description="Broadway hit",
        sources=["atgtickets.com", "act-sf.org"],
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)
    return event


def test_list_events_empty(client, db_session):
    response = client.get("/events")
    assert response.status_code == 200
    assert response.json() == []


def test_list_events_returns_event(client, sample_event):
    response = client.get("/events")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Test Event"


def test_list_events_filter_by_source(client, sample_event):
    response = client.get("/events?source=greenapplebooks.com")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["sources"] == ["greenapplebooks.com"]

    response = client.get("/events?source=citylights.com")
    assert response.status_code == 200
    assert len(response.json()) == 0


def test_list_events_filter_matches_any_of_multi_source(client, multi_source_event):
    """A multi-source event should appear under either of its sources."""
    for src in ("atgtickets.com", "act-sf.org"):
        response = client.get(f"/events?source={src}")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Oh, Mary!"
        assert set(data[0]["sources"]) == {"atgtickets.com", "act-sf.org"}

    response = client.get("/events?source=citylights.com")
    assert len(response.json()) == 0


def test_list_events_filter_by_date(client, sample_event):
    response = client.get("/events?from_date=2026-06-01T00:00:00&to_date=2026-06-02T00:00:00")
    assert response.status_code == 200
    assert len(response.json()) == 1

    response = client.get("/events?from_date=2026-07-01T00:00:00")
    assert response.status_code == 200
    assert len(response.json()) == 0


def test_get_event_by_id(client, sample_event):
    response = client.get(f"/events/{sample_event.id}")
    assert response.status_code == 200
    assert response.json()["title"] == "Test Event"


def test_get_event_not_found(client, db_session):
    response = client.get("/events/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
