import uuid
from sqlalchemy import Column, Date, DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"

    # Stable: save_events sets uuid5 of the creating source's key
    # (event_ids.event_id); uuid4 is only a fallback.
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    # tz-aware; scrapers normalize source-local times to UTC before persisting
    start_time = Column(DateTime(timezone=True), nullable=False)
    location = Column(String)
    # nullable: email/flyer/manual submissions have no source URL
    url = Column(String)
    description = Column(Text)
    # Hotlinked to the source's CDN for now; long-term we'll download at scrape
    # time and rewrite to point at our own image bucket.
    image_url = Column(String)
    # Multi-source: one physical event can appear in multiple sources' listings
    # (e.g. A.C.T. presents "Oh, Mary!" which is also listed on ATG's site).
    # When save_events sees a title+start_time match, it appends the new source
    # to this list rather than dropping the event, so the source filter shows
    # the event under either source. Stored as JSON for cross-db compatibility;
    # Postgres serializes as JSON, SQLite as TEXT.
    sources = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    # Lifecycle (feature-specs/event-lifecycle.md, §2). All nullable so rows
    # saved before them read as scheduled and never seen; db.init_db adds
    # them to an existing table.
    # scheduled / cancelled / postponed / unlisted / moved (lifecycle.py)
    status = Column(String, server_default="scheduled")
    status_at = Column(DateTime(timezone=True))   # when status last changed
    changed_at = Column(DateTime(timezone=True))  # when a tracked field last changed
    changed = Column(JSON)  # {field: previous value} for the most recent change
    seen = Column(JSON)     # {source: ISO time} of the last good scrape that listed it
    misses = Column(JSON)   # {source: n} consecutive good scrapes that should have and didn't

    # Enforce uniqueness on (url, start_time) for events that have a URL, not on
    # url alone: a single show URL legitimately hosts many performances at
    # different times (Berkeley Rep, NCTC expose no per-performance URL). NULL
    # urls (email/flyer/manual entries) are exempt so they don't collide.
    __table_args__ = (
        # Dedup looks rows up by start_time on every save.
        Index("ix_events_start_time", "start_time"),
        Index(
            "ix_events_url_start_unique",
            "url",
            "start_time",
            unique=True,
            postgresql_where=url.isnot(None),
            sqlite_where=url.isnot(None),
        ),
    )


class EventAlias(Base):
    """An event id that changed, and what it became (feature-specs/
    event-lifecycle.md, §1). `reason`: merged (duplicates merged), rekeyed
    (the one-off re-key to stable ids) or moved (rescheduled to a new
    time). Old ids keep resolving for collections and calendars that stored
    them; follow chains with event_ids.resolve."""
    __tablename__ = "event_aliases"

    old_id = Column(UUID(as_uuid=True), primary_key=True)
    new_id = Column(UUID(as_uuid=True), nullable=False)
    reason = Column(String, nullable=False)
    at = Column(DateTime(timezone=True), nullable=False)


class SubmissionCount(Base):
    """Events accepted per sender per local day, for the email-ingest cap.

    `sender_key` is a keyed HMAC of the address — the address itself is never
    stored (the repo and its logs are public; see ingest/quota.py).
    """
    __tablename__ = "submission_counts"

    sender_key = Column(String, primary_key=True)
    day = Column(Date, primary_key=True)
    events = Column(Integer, nullable=False, default=0)
