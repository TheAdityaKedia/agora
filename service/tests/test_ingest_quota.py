from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ingest import quota
from models import Base


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_sender_key_is_stable_case_insensitive_and_secret_dependent():
    k1 = quota.sender_key("Priya@Example.com ", b"secret")
    assert k1 == quota.sender_key("priya@example.com", b"secret")
    assert k1 != quota.sender_key("priya@example.com", b"other")
    assert "priya" not in k1 and len(k1) == 64


def test_counts_accumulate_per_day(session):
    d = date(2026, 10, 1)
    assert quota.used_today(session, "k", d) == 0
    quota.add(session, "k", d, 3)
    quota.add(session, "k", d, 4)
    assert quota.used_today(session, "k", d) == 7
    assert quota.used_today(session, "k", date(2026, 10, 2)) == 0


def test_prune_drops_rows_older_than_keep_days(session):
    quota.add(session, "old", date(2026, 9, 20), 1)
    quota.add(session, "new", date(2026, 9, 30), 1)
    assert quota.prune(session, today=date(2026, 10, 1), keep_days=7) == 1
    assert quota.used_today(session, "new", date(2026, 9, 30)) == 1
