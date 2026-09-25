"""Parse human-written event dates that omit the year.

Small stores often write dates by hand ("Thursday, October 8 at 6:30 pm",
"Tuesday, September 29th at 7pm"). The weekday pins down the year: of this
year and its neighbours, we take the date whose weekday matches (see
``parse_weekday_date``). Times are SF-local.
"""
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


SF_TZ = ZoneInfo("America/Los_Angeles")

_WHEN_RE = re.compile(
    r"(?P<wd>monday|tuesday|wednesday|thursday|friday|saturday|sunday),?\s+"
    r"(?P<month>[a-z]+)\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?"
    r"(?:\s+at\s+(?P<hour>\d{1,2})(?::(?P<minute>\d\d))?\s*(?P<ampm>[ap])\.?m)?",
    re.IGNORECASE,
)
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]


def parse_weekday_date(text: str, today: date) -> datetime | None:
    """'Thursday, October 8 at 6:30 pm' -> UTC datetime.

    Year candidates are last/this/next year within a plausible window (half a
    year back, a year ahead). The one whose weekday matches wins — even if it's
    in the past, so a stale listing stays past (and gets pruned) instead of
    being pushed into next year. With no weekday match, the closest date wins.
    """
    m = _WHEN_RE.search(text or "")
    if not m or m.group("month").lower() not in _MONTHS:
        return None
    month = _MONTHS.index(m.group("month").lower()) + 1
    day = int(m.group("day"))
    weekday = _WEEKDAYS.index(m.group("wd").lower())
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if -183 <= (d - today).days <= 366:
            candidates.append(d)
    if not candidates:
        return None
    matching = [d for d in candidates if d.weekday() == weekday]
    chosen = min(matching or candidates, key=lambda d: abs((d - today).days))
    hour, minute = 0, 0
    if m.group("hour"):
        hour = int(m.group("hour")) % 12 + (12 if m.group("ampm").lower() == "p" else 0)
        minute = int(m.group("minute") or 0)
    local = datetime(chosen.year, chosen.month, chosen.day, hour, minute, tzinfo=SF_TZ)
    return local.astimezone(timezone.utc)
