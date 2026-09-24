"""Kronos Quartet tour dates — kronosquartet.org (WordPress).

Kronos tours WORLDWIDE (Athens, Dublin, London, Rome, Phoenix, …); Agora is an
SF Bay Area aggregator, so the vast majority of dates get filtered out. Expect
few (often zero) upcoming Bay Area shows.

Data source (see CONTRIBUTING's priority ladder): the site is WordPress with a
custom `events` post type exposed on the REST API — a clean JSON list beats
scraping the Elementor/JetEngine DOM. Two phases:

  1. `GET /wp-json/wp/v2/events?event-status=<upcoming>` → one JSON object per
     tour date. The **title carries the city** ("San Francisco, California")
     and the **excerpt names the venue** ("… will play at Herbst Theater …").
     Filter to Bay Area cities here, before any detail fetch.
  2. For each kept event, fetch its detail page. The date + start time live as
     JetEngine dynamic fields (`.jet-listing-dynamic-field__content`): the
     first is the date ("October 11, 2026"), the next is the time
     ("AT 02:30 PM"). Trailing fields are a "related events" sidebar (other
     cities/dates) — we take the FIRST date/time only.

The REST payload has no per-event date/time and no JSON-LD, so start times come
from the detail page. Times are given in the Bay Area local zone
(America/Los_Angeles) and normalized to UTC.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "kronosquartet.org"
NAME = "Kronos Quartet"
BASE_URL = "https://kronosquartet.org"
UPCOMING_URL = "https://kronosquartet.org/upcoming-events/"
# `event-status=32` is the site's "upcoming-events" taxonomy term; we resolve it
# dynamically (below) but keep this as the documented default.
EVENTS_API_URL = "https://kronosquartet.org/wp-json/wp/v2/events"
EVENT_STATUS_API_URL = "https://kronosquartet.org/wp-json/wp/v2/event-status"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")

REQUEST_TIMEOUT = 25
API_PER_PAGE = 100
# When a detail page publishes a date but no start time, fall back to a typical
# evening concert hour rather than dropping the event (the day is what users
# browse by). Logged when it happens.
DEFAULT_LOCAL_HOUR = 19
DEFAULT_LOCAL_MINUTE = 0

# Bay Area cities/regions we keep, matched case-insensitively against the event's
# location (the WordPress title, e.g. "San Francisco, California"). Deliberately
# city-level, NOT "California" — Kronos also plays La Jolla, Santa Barbara, and
# Los Angeles, which are California but not Bay Area and must be dropped.
_BAY_AREA_RE = re.compile(
    r"\b(san francisco|sf|oakland|berkeley|san jose|bay area|alameda|emeryville"
    r"|richmond|marin|sausalito|daly city|south san francisco|fremont|hayward"
    r"|palo alto|stanford|mountain view|sunnyvale|santa clara|cupertino"
    r"|redwood city|menlo park|san mateo|burlingame|los gatos|milpitas"
    r"|union city|san rafael|novato|petaluma|santa rosa|sonoma|napa|vallejo"
    r"|walnut creek|concord|pleasanton|livermore|dublin ca|el cerrito|albany"
    r"|san leandro|pacifica|moraga|orinda|corte madera|mill valley|larkspur"
    r"|san bruno|san carlos|belmont|foster city|saratoga|campbell|los altos)\b",
    re.IGNORECASE,
)

# Date jet field, e.g. "October 11, 2026".
_DATE_RE = re.compile(r"\b([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})\b")
# Time jet field, e.g. "AT 02:30 PM".
_TIME_RE = re.compile(r"\bAT\s+(\d{1,2}):(\d{2})\s*(AM|PM)\b", re.IGNORECASE)
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def matches(url: str) -> bool:
    return "kronosquartet.org" in url


def _log(msg: str) -> None:
    print(f"[kronos] {msg}", flush=True)


def _is_bay_area(location: Optional[str]) -> bool:
    return bool(location and _BAY_AREA_RE.search(location))


def _clean(text: str) -> str:
    return " ".join(text.split()).strip()


def parse_event_list(items: list[dict]) -> list[dict]:
    """Turn REST `events` objects into candidate dicts (unfiltered).

    Each candidate: title (``Kronos Quartet — <city>``), location (the raw
    WordPress title, which carries the city and drives the Bay Area filter),
    url (the detail page), description (the excerpt: names the venue). Pure —
    the Bay Area filter is applied by the caller so it can be tested in
    isolation.
    """
    candidates: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        raw_title = _clean((it.get("title") or {}).get("rendered", "") or "")
        url = it.get("link")
        if not (raw_title and url):
            continue
        # The WordPress title is "City, State/Country"; use the city as the
        # display suffix and the whole string as the filterable location.
        city = raw_title.split(",", 1)[0].strip()
        excerpt_html = (it.get("excerpt") or {}).get("rendered", "") or ""
        description = _clean(
            BeautifulSoup(excerpt_html, "html.parser").get_text(" ", strip=True)
        ) or None
        candidates.append({
            "title": f"{NAME} — {city}" if city else NAME,
            "location": raw_title,
            "url": url,
            "description": description,
        })
    return candidates


def parse_detail_datetime(html: str) -> Optional[datetime]:
    """Extract the event's start datetime (UTC) from a detail page.

    The date is the first JetEngine dynamic field that parses as
    "<Month> <D>, <YYYY>"; the time is the first "AT HH:MM AM/PM" field. Trailing
    sidebar fields (related events) are ignored because we take the first match.
    When no time is published, falls back to a default evening hour.
    """
    soup = BeautifulSoup(html, "html.parser")
    fields = [
        _clean(f.get_text(" ", strip=True))
        for f in soup.select(".jet-listing-dynamic-field__content")
    ]

    date_val: Optional[tuple[int, int, int]] = None
    time_val: Optional[tuple[int, int]] = None
    for text in fields:
        if date_val is None:
            m = _DATE_RE.search(text)
            if m:
                month = _MONTHS.get(m.group(1).lower())
                if month:
                    date_val = (int(m.group(3)), month, int(m.group(2)))
        if time_val is None:
            tm = _TIME_RE.search(text)
            if tm:
                hour = int(tm.group(1)) % 12
                if tm.group(3).upper() == "PM":
                    hour += 12
                time_val = (hour, int(tm.group(2)))
        if date_val and time_val:
            break

    if date_val is None:
        return None
    year, month, day = date_val
    hour, minute = time_val or (DEFAULT_LOCAL_HOUR, DEFAULT_LOCAL_MINUTE)
    try:
        local = datetime(year, month, day, hour, minute, tzinfo=SOURCE_TZ)
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


# --- Network (kept out of the pure parse* functions) ---

def _resolve_upcoming_status_id() -> Optional[int]:
    """Look up the term id of the `upcoming-events` event-status taxonomy."""
    try:
        resp = requests.get(
            EVENT_STATUS_API_URL, params={"per_page": 100},
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        for term in resp.json():
            if isinstance(term, dict) and term.get("slug") == "upcoming-events":
                return term.get("id")
    except (requests.RequestException, ValueError) as e:
        _log(f"event-status lookup failed: {type(e).__name__}: {e}")
    return None


def _fetch_event_list() -> list[dict]:
    """Fetch upcoming `events` from the WordPress REST API (all pages)."""
    params = {"per_page": API_PER_PAGE}
    status_id = _resolve_upcoming_status_id()
    if status_id is not None:
        params["event-status"] = status_id
        _log(f"upcoming-events status id = {status_id}")
    else:
        _log("could not resolve upcoming-events status; fetching all events")

    items: list[dict] = []
    page = 1
    while True:
        try:
            resp = requests.get(
                EVENTS_API_URL, params={**params, "page": page},
                headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            _log(f"events fetch failed (page {page}): {type(e).__name__}: {e}")
            break
        if resp.status_code == 400:
            # WordPress returns 400 for a page past the last one.
            break
        if resp.status_code != 200:
            _log(f"events fetch HTTP {resp.status_code} (page {page}), stopping")
            break
        try:
            batch = resp.json()
        except ValueError:
            break
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        total_pages = resp.headers.get("X-WP-TotalPages")
        _log(f"events page {page}: {len(batch)} items")
        if total_pages and page >= int(total_pages):
            break
        if len(batch) < API_PER_PAGE:
            break
        page += 1
    return items


def _fetch_detail_html(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers={"User-Agent": BROWSER_UA},
                            timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None
        return resp.text
    except requests.RequestException:
        return None


def scrape(url: str = UPCOMING_URL) -> list[RawEvent]:
    """Fetch Kronos' upcoming tour dates, keep only Bay Area shows, and resolve
    each survivor's start datetime from its detail page.
    """
    _log("phase 1: fetching upcoming events from WordPress REST API")
    items = _fetch_event_list()
    candidates = parse_event_list(items)
    bay = [c for c in candidates if _is_bay_area(c["location"])]
    _log(f"phase 1: {len(candidates)} total events, {len(bay)} in the Bay Area")

    events: list[RawEvent] = []
    for i, c in enumerate(bay, 1):
        _log(f"phase 2: detail {i}/{len(bay)} — {c['location']}")
        html = _fetch_detail_html(c["url"])
        start_time = parse_detail_datetime(html) if html else None
        if start_time is None:
            _log(f"  no parseable date for {c['url']}, skipping")
            continue
        events.append(RawEvent(
            title=c["title"],
            start_time=start_time,
            location=c["location"],
            url=c["url"],
            description=c["description"],
            image_url=None,
        ))
        time.sleep(0.3)

    _log(f"done: {len(events)} Bay Area events")
    return events
