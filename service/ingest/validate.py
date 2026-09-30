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
    text = _scrub(desc or "").strip()
    if cost and cost.strip():
        text = f"{text}\n\nCost: {cost.strip()}".strip()
    return text[:MAX_DESCRIPTION] or None


def _event_url(url: str | None) -> str | None:
    """Add a missing scheme (flyers say "WWW.ZoukSF.COM")."""
    url = (url or "").strip()
    if not url:
        return None
    return url if url.lower().startswith(("http://", "https://")) else "https://" + url


# Backstop for personal details in screenshots (e.g. a WhatsApp poster's phone
# number) — the prompt asks the model to leave them out; this doesn't rely on it.
_PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _scrub(text: str) -> str:
    text = _EMAIL_RE.sub("", _PHONE_RE.sub("", text))
    return re.sub(r"[ \t]{2,}", " ", text)


# Reject a location only when it *positively* names somewhere outside the Bay
# Area. Venue names ("Salesforce Park Main Plaza", "Dolores Park") name no city
# and are allowed.
_US_STATES = {"AL", "AK", "AZ", "AR", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
              "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
              "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX",
              "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"}
_STATE_RE = re.compile(r"(?:,\s*|\s)([A-Z]{2})(?=\s+\d{5}|\s*,|\s*$)")
_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
_BAY_ZIP_PREFIXES = ("94", "950", "951", "954")  # SF/East Bay/Peninsula, South Bay, North Bay
_ELSEWHERE_CA_RE = re.compile(
    r"\b(los angeles|san diego|sacramento|fresno|santa monica|long beach|anaheim|pasadena|"
    r"hollywood|irvine|riverside|bakersfield|santa barbara|palm springs|monterey|lake tahoe|"
    r"davis|stockton|modesto)\b", re.I)


def _outside_bay_area(location: str | None) -> bool:
    if not location or is_bay_area(location):
        return False
    if any(st in _US_STATES for st in _STATE_RE.findall(location)):
        return True
    zips = _ZIP_RE.findall(location)
    if zips and not any(z.startswith(_BAY_ZIP_PREFIXES) for z in zips):
        return True
    return bool(_ELSEWHERE_CA_RE.search(location))


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
