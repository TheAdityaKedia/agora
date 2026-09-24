"""Shared iCalendar (.ics) feed parser.

Many platforms expose a public iCal feed — Sched (``<event>.sched.com/all.ics``),
Squarespace (``?format=ical``), The Events Calendar, etc. Each ``VEVENT`` carries
SUMMARY, DTSTART/DTEND, LOCATION, DESCRIPTION, and URL, so a feed is a clean,
per-occurrence source. Any such source plugs in with a thin wrapper (see
scrapers/litquake.py) supplying its feed URL.

We parse the feed by hand (no icalendar dependency): unfold folded lines, split
VEVENT blocks, decode ICS escaping, and normalize DTSTART (UTC ``…Z``, a
``TZID=`` local time, or an all-day ``VALUE=DATE``) to UTC.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


REQUEST_TIMEOUT = 25


def _log(msg: str) -> None:
    print(f"[ics] {msg}", flush=True)


def _unescape(value: str) -> str:
    """Decode ICS text escaping (RFC 5545 §3.3.11)."""
    out = []
    i = 0
    while i < len(value):
        c = value[i]
        if c == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _clean_description(raw: str) -> str | None:
    text = _unescape(raw)
    # Sched descriptions embed HTML entities/tags — flatten to plain text.
    text = BeautifulSoup(html.unescape(text), "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _parse_dt(raw_key: str, value: str) -> datetime | None:
    """Parse a DTSTART value (with its params) to a tz-aware UTC datetime."""
    value = value.strip()
    params = raw_key.split(";")[1:]
    tzid = next((p.split("=", 1)[1] for p in params if p.upper().startswith("TZID=")), None)
    is_date = any(p.upper() == "VALUE=DATE" for p in params) or (len(value) == 8 and value.isdigit())
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        if is_date:
            naive = datetime.strptime(value[:8], "%Y%m%d")
            tz = timezone.utc
        else:
            naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
            tz = timezone.utc
            if tzid:
                try:
                    tz = ZoneInfo(tzid)
                except ZoneInfoNotFoundError:
                    tz = timezone.utc
        return naive.replace(tzinfo=tz).astimezone(timezone.utc)
    except ValueError:
        return None


def _unfold(text: str) -> str:
    """Join RFC 5545 folded continuation lines (a line starting with space/tab)."""
    return re.sub(r"\r?\n[ \t]", "", text)


def _iter_vevents(text: str):
    for block in _unfold(text).split("BEGIN:VEVENT")[1:]:
        yield block.split("END:VEVENT")[0]


def _fields(block: str) -> dict[str, tuple[str, str]]:
    """Map field NAME -> (raw_key_with_params, value) for one VEVENT block."""
    fields: dict[str, tuple[str, str]] = {}
    for line in block.strip().splitlines():
        if ":" not in line:
            continue
        raw_key, value = line.split(":", 1)
        name = raw_key.split(";")[0].upper()
        fields[name] = (raw_key, value)
    return fields


def parse_ics(text: str, *, fallback_location: str | None = None) -> list[RawEvent]:
    """Parse an iCal feed into RawEvents (one per VEVENT). Pure — no network."""
    events: list[RawEvent] = []
    for block in _iter_vevents(text):
        f = _fields(block)
        if "SUMMARY" not in f or "DTSTART" not in f:
            continue
        title = _unescape(f["SUMMARY"][1]).strip()
        start_time = _parse_dt(*f["DTSTART"])
        if not (title and start_time):
            continue
        location = _unescape(f["LOCATION"][1]).strip() if "LOCATION" in f else None
        description = _clean_description(f["DESCRIPTION"][1]) if "DESCRIPTION" in f else None
        url = f["URL"][1].strip() if "URL" in f else None
        events.append(RawEvent(
            title=title,
            start_time=start_time,
            location=location or fallback_location,
            url=url,
            description=description,
            image_url=None,
        ))
    return events


def scrape_ics(ics_url: str, *, fallback_location: str | None = None) -> list[RawEvent]:
    """Fetch and parse an iCal feed."""
    try:
        resp = requests.get(ics_url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        _log(f"fetch failed for {ics_url}: {type(e).__name__}: {e}")
        return []
    events = parse_ics(resp.text, fallback_location=fallback_location)
    _log(f"{ics_url}: {len(events)} events")
    return events
