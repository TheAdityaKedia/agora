import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api import app
from models import Base


@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def client():
    return TestClient(app)


def test_list_events_returns_200(client):
    response = client.get("/events")
    assert response.status_code == 200


def test_list_events_returns_list(client):
    response = client.get("/events")
    assert isinstance(response.json(), list)
