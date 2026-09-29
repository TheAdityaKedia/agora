import uuid
from sqlalchemy import Column, Date, DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"

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

    # Enforce uniqueness on (url, start_time) for events that have a URL, not on
    # url alone: a single show URL legitimately hosts many performances at
    # different times (Berkeley Rep, NCTC expose no per-performance URL). NULL
    # urls (email/flyer/manual entries) are exempt so they don't collide.
    __table_args__ = (
        Index(
            "ix_events_url_start_unique",
            "url",
            "start_time",
            unique=True,
            postgresql_where=url.isnot(None),
            sqlite_where=url.isnot(None),
        ),
    )


class SubmissionCount(Base):
    """Events accepted per sender per local day, for the email-ingest cap.

    `sender_key` is a keyed HMAC of the address — the address itself is never
    stored (the repo and its logs are public; see ingest/quota.py).
    """
    __tablename__ = "submission_counts"

    sender_key = Column(String, primary_key=True)
    day = Column(Date, primary_key=True)
    events = Column(Integer, nullable=False, default=0)
