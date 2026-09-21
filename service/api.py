from datetime import datetime
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from db import get_session
from models import Event

app = FastAPI(title="Agora")


class EventResponse(BaseModel):
    id: UUID
    title: str
    start_time: datetime
    location: str | None
    url: str | None
    description: str | None
    image_url: str | None
    sources: list[str]
    created_at: datetime

    model_config = {"from_attributes": True}


@app.get("/events", response_model=list[EventResponse])
def list_events(
    source: str | None = Query(None),
    from_date: datetime | None = Query(None),
    to_date: datetime | None = Query(None),
):
    session = get_session()
    try:
        q = session.query(Event).order_by(Event.start_time)
        if from_date:
            q = q.filter(Event.start_time >= from_date)
        if to_date:
            q = q.filter(Event.start_time <= to_date)
        events = q.all()
        # `sources` is a JSON column; portable containment across Postgres +
        # SQLite is easier in Python than in SQL for our small dataset.
        if source:
            events = [e for e in events if source in (e.sources or [])]
        return events
    finally:
        session.close()


@app.get("/events/{event_id}", response_model=EventResponse)
def get_event(event_id: UUID):
    session = get_session()
    try:
        event = session.query(Event).filter_by(id=event_id).first()
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        return event
    finally:
        session.close()
