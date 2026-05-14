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
        source="greenapplebooks.com",
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

    response = client.get("/events?source=citylights.com")
    assert response.status_code == 200
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
