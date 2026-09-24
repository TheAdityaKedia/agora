"""Expand a recurring schedule into concrete dated occurrences.

Agora stores dated events; recurring sources (bar trivia, weekly nights) publish
a cadence ("every Thursday 7:30pm") plus a next-occurrence date. This helper
turns that into the actual upcoming datetimes within a short rolling window, so
each occurrence becomes an ordinary event on the day-grouped calendar.

Only cadences we can expand without guessing are expanded: ISO-8601 weekly/daily
`repeatFrequency` (P1W, P2W, P3D, …). Monthly / unknown / missing frequency
emits just the single given anchor date (no fabricated cadence — see the
"never fabricate performances" rule in CONTRIBUTING.md).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Window is anchored to the audience region's calendar day, matching the
# exporter (events show from the start of today, local).
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
DEFAULT_HORIZON_DAYS = 28

_FREQ_RE = re.compile(r"^P(\d+)([WD])$")


def _step_for(repeat_frequency: str | None) -> timedelta | None:
    """Parse an ISO-8601 duration like 'P1W'/'P2W'/'P3D' into a timedelta.
    Returns None for monthly/unknown/missing (caller emits only the anchor)."""
    if not repeat_frequency:
        return None
    m = _FREQ_RE.match(repeat_frequency.strip())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if n <= 0:
        return None
    return timedelta(weeks=n) if unit == "W" else timedelta(days=n)


def expand_occurrences(
    first_start,
    repeat_frequency: str | None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    now: datetime | None = None,
) -> list[datetime]:
    """Return tz-aware UTC datetimes for each occurrence within the window.

    Window = [start of today (local), now + horizon_days]. `first_start` is the
    anchor (ISO string or datetime, tz-aware). If it's stale (before the
    window), roll forward by the cadence; if it's beyond the window, return [].
    """
    now = now or datetime.now(timezone.utc)
    if isinstance(first_start, str):
        first = datetime.fromisoformat(first_start)
    else:
        first = first_start
    if first.tzinfo is None:
        first = first.replace(tzinfo=timezone.utc)
    first = first.astimezone(timezone.utc)

    floor = datetime.combine(now.astimezone(LOCAL_TZ).date(), datetime.min.time(),
                             tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    ceil = now + timedelta(days=horizon_days)

    step = _step_for(repeat_frequency)
    if step is None:
        return [first] if floor <= first <= ceil else []

    occ = first
    # Roll a stale anchor forward to the first occurrence within the window.
    while occ < floor:
        occ += step
    out: list[datetime] = []
    while occ <= ceil:
        out.append(occ)
        occ += step
    return out
