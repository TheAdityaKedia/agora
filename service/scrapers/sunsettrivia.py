"""Sunset Trivia — a trivia operator's venue directory.

Sunset Trivia (sunsettrivia.com) runs weekly pub quizzes and lists all its
venues at /locations. It's a Next.js App-Router app: the venue data ships in
the RSC streaming payload (`self.__next_f.push([1,"…json…"])`) as a `venues`
array, each with name, address, city/zip/region, `dayOfWeek`, `time`,
`eventFrequency`, and an `aiDescription`.

Unlike SF Bar Guide there's no concrete next-occurrence date — just a weekday +
time — so we compute the next occurrence (recurrence.next_weekly_start) and
expand weekly. Scoped to the Bay Area (this is an SF calendar). See
feature-specs/recurring-events.md.
"""
from __future__ import annotations

import json
import re

import requests

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA
from scrapers.recurrence import next_weekly_start, expand_occurrences, DEFAULT_HORIZON_DAYS

SOURCE = "sunsettrivia.com"
NAME = "Sunset Trivia"
LOCATIONS_URL = "https://sunsettrivia.com/locations"
REQUEST_TIMEOUT = 25

_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')


def _log(msg: str) -> None:
    print(f"[sunsettrivia] {msg}", flush=True)


def matches(url: str) -> bool:
    return "sunsettrivia.com" in url


def parse_venues(html: str) -> list[dict]:
    """Extract the `venues` array from the RSC streaming payload."""
    blob = "".join(_PUSH_RE.findall(html))
    if not blob:
        return []
    try:
        blob = blob.encode().decode("unicode_escape")
    except (UnicodeDecodeError, ValueError):
        return []
    k = blob.find('"venues":')
    if k == -1:
        return []
    start = blob.find("[", k)
    if start == -1:
        return []
    depth = 0
    for j in range(start, len(blob)):
        c = blob[j]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(blob[start:j + 1])
                except json.JSONDecodeError:
                    return []
    return []


def _is_bay_area(v: dict) -> bool:
    if (v.get("region") or "").strip().lower() == "bay area":
        return True
    return str(v.get("zip") or "").startswith(("94", "95"))


def _venue_events(v: dict, now=None) -> list[RawEvent]:
    """Expand one venue's weekly trivia into dated occurrences (or [] if the
    cadence isn't weekly or the day/time can't be parsed)."""
    if (v.get("eventFrequency") or "").strip().lower() != "weekly":
        return []  # only weekly is unambiguous; skip monthly/other
    first = next_weekly_start(v.get("dayOfWeek"), v.get("time"), now=now)
    if first is None:
        return []
    name = v.get("name") or "a bar"
    location = v.get("address") or name
    url = v.get("website") or LOCATIONS_URL
    desc = v.get("aiDescription") or None
    out: list[RawEvent] = []
    for occ in expand_occurrences(first, "P1W", DEFAULT_HORIZON_DAYS, now=now):
        out.append(RawEvent(
            title=f"Trivia Night at {name}",  # venue in title → unique dedup
            start_time=occ,
            location=location,
            url=url,
            description=desc,
            image_url=None,
        ))
    return out


def _fetch(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def scrape(url: str = LOCATIONS_URL, now=None) -> list[RawEvent]:
    _log(f"fetching {url}")
    try:
        html = _fetch(url)
    except requests.RequestException as e:
        _log(f"fetch failed: {type(e).__name__}: {e}")
        return []
    venues = parse_venues(html)
    bay = [v for v in venues if _is_bay_area(v)]
    _log(f"{len(venues)} venues, {len(bay)} Bay Area")
    events: list[RawEvent] = []
    for v in bay:
        events.extend(_venue_events(v, now=now))
    _log(f"done: {len(events)} occurrences from {len(bay)} Bay Area venues")
    return events
