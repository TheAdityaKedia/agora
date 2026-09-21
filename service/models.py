import uuid
from sqlalchemy import Column, String, DateTime, Text, Index, JSON
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

    # Enforce URL uniqueness only for events that have one (scraped events).
    # NULL urls (email/flyer/manual entries) are exempt so they don't collide.
    __table_args__ = (
        Index(
            "ix_events_url_unique",
            "url",
            unique=True,
            postgresql_where=url.isnot(None),
            sqlite_where=url.isnot(None),
        ),
    )
