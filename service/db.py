import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from models import Base

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        # pool_pre_ping: a hosted DB (Neon) drops idle connections, and a long
        # scrape can leave a pooled connection idle for many minutes before
        # the next save. Ping on checkout so a dead one is replaced, not used.
        _engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    return _engine


def get_session():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


# Columns added to `events` after the table existed (feature-specs/
# event-lifecycle.md). create_all only creates missing tables, so the
# Postgres DB (Neon) gets them here; SQLite (tests) builds the table fresh.
EVENT_COLUMNS = {
    "status": "VARCHAR DEFAULT 'scheduled'",
    "status_at": "TIMESTAMP WITH TIME ZONE",
    "changed_at": "TIMESTAMP WITH TIME ZONE",
    "changed": "JSON",
    "seen": "JSON",
    "misses": "JSON",
}


def column_migrations(dialect: str) -> list[str]:
    if dialect != "postgresql":
        return []
    return [f"ALTER TABLE events ADD COLUMN IF NOT EXISTS {name} {ddl}"
            for name, ddl in EVENT_COLUMNS.items()]


def init_db():
    engine = get_engine()
    Base.metadata.create_all(engine)
    statements = column_migrations(engine.dialect.name)
    if statements:
        with engine.begin() as conn:
            for sql in statements:
                conn.execute(text(sql))
    # create_all only creates missing *tables*; add indexes introduced later to
    # tables that already exist (e.g. ix_events_start_time).
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(engine, checkfirst=True)
