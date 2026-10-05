"""Shared helpers for venues ticketing through OvationTix (AudienceView).

OvationTix's public read API needs no browser — just the venue's numeric
``clientId`` header (visible in its ``ci.ovationtix.com/<clientId>`` links):

- ``CalendarProductions`` — per-day productions, each with its showtimes
  (wall-clock SF time), visibility and cancellation flags, and a logo file id.
- ``Production?expandPerformances=summary`` — the catalog: HTML description
  and venue name per production id.

parse_events joins the two into one RawEvent per visible showtime. Two
venue-agnostic cleanups: "test event" productions are dropped, and a
timed-entry production (an exhibition selling a slot every half hour) is
collapsed to its earliest slot, since dozens of identical slots a day are
noise in a calendar.

scrapers/zspace.py predates this module and keeps its own copy (it adds
homepage-poster image matching). New venues: a thin wrapper passing the
client id and fallback address (see scrapers/oaklandtheaterproject.py).
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


API_BASE = "https://web.ovationtix.com/trs/api/rest"
CALENDAR_URL = f"{API_BASE}/CalendarProductions"
PRODUCTIONS_URL = f"{API_BASE}/Production?expandPerformances=summary"
PRODUCTION_URL = "https://ci.ovationtix.com/{client}/production/{id}"
IMAGE_URL = f"{API_BASE}/ClientFile({{file}})"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 30
# More showtimes than this on one day ⇒ timed-entry slots, not performances.
TIMED_ENTRY_PER_DAY = 4

_TEST_RE = re.compile(r"\btest event\b", re.I)


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    text = re.sub(r"[﻿​\xa0]", " ", text)
    return re.sub(r"\s+", " ", text).strip() or None


def _parse_start(value: str | None) -> datetime | None:
    try:
        naive = datetime.strptime(value or "", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=SOURCE_TZ).astimezone(timezone.utc)


def parse_events(calendar: list[dict], productions: list[dict], *, client_id: str,
                 fallback_location: str) -> list[RawEvent]:
    """Join calendar showtimes with the production catalog. Pure."""
    catalog = {p.get("id"): p for p in productions or [] if isinstance(p, dict)}
    rows: list[tuple[int, str, RawEvent]] = []
    for day in calendar or []:
        for prod in day.get("productions") or []:
            pid = prod.get("productionId")
            detail = catalog.get(pid, {})
            title = _clean(prod.get("name") or detail.get("productionName"))
            if not title or _TEST_RE.search(title):
                continue
            venue = _clean((detail.get("venue") or {}).get("name"))
            logo = prod.get("logoFile")
            for st in prod.get("showtimes") or []:
                if st.get("isVisible") is False:
                    continue
                start = _parse_start(st.get("performanceStartTime"))
                if not start:
                    continue
                rows.append((pid, day.get("date") or "", RawEvent(
                    title=title,
                    start_time=start,
                    location=f"{venue}, {fallback_location}" if venue and venue not in fallback_location
                    else fallback_location,
                    url=PRODUCTION_URL.format(client=client_id, id=pid) if pid is not None else None,
                    description=_clean(detail.get("description")),
                    image_url=IMAGE_URL.format(file=logo) if logo else None,
                    status="cancelled" if st.get("isCancelled") else None,
                )))

    per_day = Counter((pid, date) for pid, date, _ in rows)
    timed = {pid for (pid, _), n in per_day.items() if n > TIMED_ENTRY_PER_DAY}
    events: list[RawEvent] = []
    first_timed: dict[int, RawEvent] = {}
    for pid, _, ev in rows:
        if pid in timed:
            if pid not in first_timed or ev.start_time < first_timed[pid].start_time:
                first_timed[pid] = ev
        else:
            events.append(ev)
    events.extend(first_timed.values())
    return sorted(events, key=lambda e: e.start_time)


def _fetch(url: str, client_id: str) -> list:
    resp = requests.get(url, headers={"clientId": client_id, "Accept": "application/json",
                                      "User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def scrape_client(client_id: str, *, fallback_location: str, tag: str = "ovationtix") -> list[RawEvent]:
    try:
        calendar = _fetch(CALENDAR_URL, client_id)
        productions = _fetch(PRODUCTIONS_URL, client_id)
    except (requests.RequestException, ValueError) as e:
        print(f"[{tag}] API fetch failed: {e}", flush=True)
        return []
    events = parse_events(calendar, productions, client_id=client_id,
                          fallback_location=fallback_location)
    print(f"[{tag}] {len(events)} events from {len(calendar)} calendar days", flush=True)
    return events
