from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import dedupe_existing
from models import Base, Event

T = datetime(2026, 10, 5, 2, 0, tzinfo=timezone.utc)
LOC = "City Lights Booksellers, 261 Columbus Ave, San Francisco"


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _add(s, title, sources, loc=LOC, start=T):
    e = Event(title=title, start_time=start, location=loc, url=None, description=None,
              sources=sources, created_at=T)
    s.add(e); s.commit()
    return e


def test_plan_keeps_the_earlier_source_and_unions_sources(session):
    order = ["City Lights Booksellers", "Litquake", "Noe Valley Books"]
    keep = _add(session, "A Celebration of César Vallejo’s Trilce", ["City Lights Booksellers"])
    dup = _add(session, "A Celebration of César Vallejo's Trilce", ["Litquake", "Green Apple Books"])
    _add(session, "Jazz Jam", ["El Rio"], loc="El Rio, 3158 Mission St")  # unrelated, same time
    plan = dedupe_existing.plan(session, source_order=order)
    assert [(p.keep.id, [d.id for d in p.drop]) for p in plan] == [(keep.id, [dup.id])]
    assert plan[0].sources == ["City Lights Booksellers", "Litquake", "Green Apple Books"]


def test_same_source_rows_are_never_merged(session):
    _add(session, "Zouk with Anna", ["Church of Zouk"])
    _add(session, "Advanced Zouk with Anna", ["Church of Zouk"])
    assert dedupe_existing.plan(session, source_order=[]) == []


def test_apply_merges_and_deletes(session):
    _add(session, "Lindy West", ["City Arts & Lectures"], loc="Sydney Goldstein Theater")
    _add(session, "Offsite: Lindy West at City Arts & Lectures", ["Green Apple Books"], loc=None)
    p = dedupe_existing.plan(session, source_order=["City Arts & Lectures", "Green Apple Books"])
    assert dedupe_existing.apply(session, p) == 1
    rows = session.query(Event).all()
    assert len(rows) == 1 and rows[0].title == "Lindy West"
    assert rows[0].sources == ["City Arts & Lectures", "Green Apple Books"]
