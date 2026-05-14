import uuid
from sqlalchemy import Column, String, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    start_time = Column(DateTime, nullable=False)
    location = Column(String)
    url = Column(String, unique=True, nullable=False)
    description = Column(Text)
    source = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)
