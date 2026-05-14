from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from main import load_sources, save_events
from models import Base, Event
from scrapers.greenapple import RawEvent


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    with patch("main.get_session", return_value=session):
        yield session

    session.close()


def test_load_sources_returns_list(tmp_path):
    sources_file = tmp_path / "sources.txt"
    sources_file.write_text("https://greenapplebooks.com/events\n")
    with patch("main.SOURCES_FILE", sources_file):
        sources = load_sources()
    assert sources == ["https://greenapplebooks.com/events"]


def test_load_sources_ignores_blank_lines(tmp_path):
    sources_file = tmp_path / "sources.txt"
    sources_file.write_text("\nhttps://greenapplebooks.com/events\n\n")
    with patch("main.SOURCES_FILE", sources_file):
        sources = load_sources()
    assert len(sources) == 1


def test_load_sources_multiple(tmp_path):
    sources_file = tmp_path / "sources.txt"
    sources_file.write_text("https://a.com\nhttps://b.com\n")
    with patch("main.SOURCES_FILE", sources_file):
        sources = load_sources()
    assert len(sources) == 2


def _make_raw_event(url="https://greenapplebooks.com/event/1") -> RawEvent:
    return RawEvent(
        title="Test Event",
        start_time=datetime(2026, 6, 1, 19, 0),
        location="1231 9th Ave, San Francisco",
        url=url,
        description=None,
    )


def test_save_events_persists_to_db(db_session):
    saved, skipped = save_events([_make_raw_event()], source="greenapplebooks.com")
    assert saved == 1
    assert skipped == 0
    assert db_session.query(Event).count() == 1


def test_save_events_skips_duplicate_url(db_session):
    raw = _make_raw_event()
    save_events([raw], source="greenapplebooks.com")
    saved, skipped = save_events([raw], source="greenapplebooks.com")
    assert saved == 0
    assert skipped == 1
    assert db_session.query(Event).count() == 1


def test_save_events_saves_correct_fields(db_session):
    save_events([_make_raw_event()], source="greenapplebooks.com")
    event = db_session.query(Event).first()
    assert event.title == "Test Event"
    assert event.source == "greenapplebooks.com"
    assert event.url == "https://greenapplebooks.com/event/1"
