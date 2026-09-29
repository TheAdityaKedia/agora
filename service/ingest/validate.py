"""Candidate event dicts (from the LLM) → RawEvents, or a reason for the reply."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timezone

from ingest import LOCAL_TZ
from scrapers.bay_area import is_bay_area
from scrapers.base import RawEvent
from scrapers.recurrence import expand_occurrences, next_weekly_start

MAX_DESCRIPTION = 2000
RECURRENCE_HORIZON_DAYS = 56
NO_DATE = "couldn't find a date"
NO_TIME = "couldn't find a start time"
PAST = "this event already happened"
NOT_BAY = "not in the Bay Area"
NO_TITLE = "couldn't find the event's name"

_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def _parse_time(s: str | None) -> time | None:
    m = _TIME_RE.match(s or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return time(int(m.group(1)), int(m.group(2)))


def _parse_date(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _start_of_today_utc(now: datetime) -> datetime:
    today = now.astimezone(LOCAL_TZ).date()
    return datetime.combine(today, time.min, LOCAL_TZ).astimezone(timezone.utc)


def _location(venue: str | None, address: str | None) -> str | None:
    venue, address = (venue or "").strip(), (address or "").strip()
    parts = ([venue] if venue and venue not in address else []) + ([address] if address else [])
    return ", ".join(parts) or None


def _description(desc: str | None, cost: str | None) -> str | None:
    text = (desc or "").strip()
    if cost and cost.strip():
        text = f"{text}\n\nCost: {cost.strip()}".strip()
    return text[:MAX_DESCRIPTION] or None


# An address "names a place" when it has a comma, a state code or a ZIP — only
# then can it be judged outside the Bay Area. A bare venue ("Dolores Park")
# names no city and is allowed.
_NAMES_PLACE_RE = re.compile(r",|\b[A-Z]{2}\b|\b\d{5}\b")


def _outside_bay_area(location: str | None) -> bool:
    return bool(location) and not is_bay_area(location) and bool(_NAMES_PLACE_RE.search(location))


def _event_url(url: str | None) -> str | None:
    """Add a missing scheme; drop bare homepages. A homepage shared by several
    same-time events would make dedup (url + start) merge distinct events."""
    from urllib.parse import urlparse
    url = (url or "").strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url if urlparse(url).path.strip("/") else None


def check_event(e: RawEvent, *, now: datetime) -> str | None:
    if e.start_time < _start_of_today_utc(now):
        return PAST
    if _outside_bay_area(e.location):
        return NOT_BAY
    return None


def _starts(c: dict, now: datetime) -> tuple[list[datetime], str | None]:
    rec = c.get("recurrence") or None
    if rec and rec.get("weekday") and _parse_time(rec.get("time")):
        t = _parse_time(rec["time"])
        anchor = next_weekly_start(rec["weekday"], t.strftime("%I:%M %p").lstrip("0"), now=now)
        if anchor is None:
            return [], NO_DATE
        starts = expand_occurrences(anchor, "P1W", horizon_days=RECURRENCE_HORIZON_DAYS, now=now)
        until = _parse_date(rec.get("until"))
        if until:
            starts = [s for s in starts if s.astimezone(LOCAL_TZ).date() <= until]
        return starts, (None if starts else PAST)
    d = _parse_date(c.get("date"))
    if d is None:
        return [], NO_DATE
    t = _parse_time(c.get("start_time"))
    if t is None:
        return [], NO_TIME
    return [datetime.combine(d, t, LOCAL_TZ).astimezone(timezone.utc)], None


def candidate_to_events(c: dict, *, now: datetime, url: str | None = None) -> tuple[list[RawEvent], str | None]:
    title = (c.get("title") or "").strip()
    if not title:
        return [], NO_TITLE
    starts, reason = _starts(c, now)
    if reason:
        return [], reason
    location = _location(c.get("venue"), c.get("address"))
    events = [RawEvent(title=title, start_time=s, location=location,
                       url=_event_url(url or c.get("url")),
                       description=_description(c.get("description"), c.get("cost_text")))
              for s in starts]
    for e in events:
        r = check_event(e, now=now)
        if r == NOT_BAY:
            return [], NOT_BAY
    events = [e for e in events if check_event(e, now=now) is None]
    return (events, None) if events else ([], PAST)
