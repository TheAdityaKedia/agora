import uuid
from sqlalchemy import Column, String, DateTime, Text, Index
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
    source = Column(String, nullable=False)
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
