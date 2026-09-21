from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import LOOKAHEAD_DAYS
from main import load_sources, run, save_events
from models import Base, Event
from scrapers.base import RawEvent


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
        start_time=datetime(2026, 6, 1, 19, 0, tzinfo=timezone.utc),
        location="1231 9th Ave, San Francisco",
        url=url,
        description=None,
    )


def test_save_events_persists_to_db(db_session):
    saved, merged, skipped = save_events([_make_raw_event()], source="greenapplebooks.com")
    assert (saved, merged, skipped) == (1, 0, 0)
    assert db_session.query(Event).count() == 1


def test_save_events_skips_same_source_re_scrape(db_session):
    raw = _make_raw_event()
    save_events([raw], source="greenapplebooks.com")
    saved, merged, skipped = save_events([raw], source="greenapplebooks.com")
    assert (saved, merged, skipped) == (0, 0, 1)
    assert db_session.query(Event).count() == 1
    # Source stays a single-item list, not duplicated.
    assert db_session.query(Event).first().sources == ["greenapplebooks.com"]


def test_save_events_saves_correct_fields(db_session):
    save_events([_make_raw_event()], source="greenapplebooks.com")
    event = db_session.query(Event).first()
    assert event.title == "Test Event"
    assert event.sources == ["greenapplebooks.com"]
    assert event.url == "https://greenapplebooks.com/event/1"


def test_save_events_merges_sources_on_title_and_date_match(db_session):
    """One physical event, listed by two different sources: dedup merges the
    second source into the existing row rather than dropping the event.

    Before this change, filtering by the second source hid the event entirely.
    """
    original = _make_raw_event(url="https://greenapplebooks.com/event/1")
    duplicate = _make_raw_event(url="https://differenturl.com/event/99")
    save_events([original], source="greenapplebooks.com")
    saved, merged, skipped = save_events([duplicate], source="email")
    assert (saved, merged, skipped) == (0, 1, 0)
    assert db_session.query(Event).count() == 1
    event = db_session.query(Event).first()
    # Both sources are now attached to the single event row.
    assert event.sources == ["greenapplebooks.com", "email"]
    # URL is retained from the first save; second URL is not stored.
    assert event.url == "https://greenapplebooks.com/event/1"


def test_save_events_urlless_events_dont_dedup_against_each_other(db_session):
    """Regression: filter_by(url=None) matched every prior URL-less event and
    swallowed 43/44 Black Bird events in a live run. URL-less events should
    only be deduped by title+start_time.
    """
    ev_a = RawEvent(title="Event A", start_time=datetime(2026, 10, 1, 19, 0, tzinfo=timezone.utc),
                    location=None, url=None, description=None)
    ev_b = RawEvent(title="Event B", start_time=datetime(2026, 10, 2, 19, 0, tzinfo=timezone.utc),
                    location=None, url=None, description=None)
    saved, merged, skipped = save_events([ev_a, ev_b], source="test")
    assert (saved, merged, skipped) == (2, 0, 0)


def test_run_source_filter_only_scrapes_matching_urls(db_session, tmp_path, monkeypatch):
    """`run(source_filters=[...])` should only dispatch to sources whose URL
    contains one of the given substrings.
    """
    sources_file = tmp_path / "sources.txt"
    sources_file.write_text(
        "https://greenapplebooks.com/events\n"
        "https://gamh.com/calendar/\n"
        "https://www.thefillmore.com/shows\n"
    )
    monkeypatch.setattr("main.SOURCES_FILE", sources_file)
    monkeypatch.setattr("main.DEFAULT_EVENTS_JSON", tmp_path / "events.json")
    monkeypatch.setattr("main.init_db", lambda: None)  # skip real db init
    dispatched: list[str] = []
    monkeypatch.setattr("main.scrape_and_save", lambda url: dispatched.append(url))
    # Don't hit the real DB — patch the exporter to a no-op writer.
    monkeypatch.setattr("main.export_json", lambda path: 0)

    run(source_filters=["gamh.com", "thefillmore.com"])

    assert dispatched == [
        "https://gamh.com/calendar/",
        "https://www.thefillmore.com/shows",
    ]


def test_run_no_filter_scrapes_all(db_session, tmp_path, monkeypatch):
    sources_file = tmp_path / "sources.txt"
    sources_file.write_text("https://a.com\nhttps://b.com\n")
    monkeypatch.setattr("main.SOURCES_FILE", sources_file)
    monkeypatch.setattr("main.DEFAULT_EVENTS_JSON", tmp_path / "events.json")
    monkeypatch.setattr("main.init_db", lambda: None)
    dispatched: list[str] = []
    monkeypatch.setattr("main.scrape_and_save", lambda url: dispatched.append(url))
    monkeypatch.setattr("main.export_json", lambda path: 0)

    run(source_filters=None)
    assert dispatched == ["https://a.com", "https://b.com"]


def test_save_events_drops_events_past_horizon(db_session):
    far_future = datetime.now(timezone.utc) + timedelta(days=LOOKAHEAD_DAYS + 30)
    within = datetime.now(timezone.utc) + timedelta(days=30)
    raw_far = RawEvent(title="Far future", start_time=far_future, location=None,
                       url="https://example.com/far", description=None)
    raw_near = RawEvent(title="Near", start_time=within, location=None,
                        url="https://example.com/near", description=None)
    saved, merged, skipped = save_events([raw_far, raw_near], source="test")
    assert (saved, merged, skipped) == (1, 0, 1)
    titles = [e.title for e in db_session.query(Event).all()]
    assert titles == ["Near"]
