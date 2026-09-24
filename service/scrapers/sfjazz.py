"""SFJAZZ Center events scraper.

SFJAZZ's public site (sfjazz.org) is behind Cloudflare that 403s *every*
document request — even a real headless browser (307→403) — so the calendar
can't be scraped directly. But the site is an Umbraco build hosted by Adage
Technologies, and its calendar loads from a clean JSON API on the origin host,
which is NOT Cloudflare-fronted:

    https://sfjazz-redesign-stage.adagetech.net/ace-api/events/?startDate=…&endDate=…

One call returns the whole season (one item per performance — multi-night runs
are already split by date), so `scrape()` hits it once, no browser needed.

NOTE: that origin host is a staging URL discovered via robots.txt; if it goes
away, fall back to a residential-proxy / CF-bypass fetch of the production
calendar. Image and detail URLs point at production sfjazz.org (stable, and
they load fine in a user's browser). The API's `eventDate` carries a wrong
offset (-05:00), so we build the time from the display date + time strings as
Pacific.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests

from config import LOOKAHEAD_DAYS
from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "sfjazz.org"
NAME = "SFJAZZ Center"
BASE_URL = "https://www.sfjazz.org"  # for user-facing image + detail URLs
ACE_API = "https://sfjazz-redesign-stage.adagetech.net/ace-api/events/"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "SFJAZZ Center, 201 Franklin St, San Francisco, CA 94102"
REQUEST_TIMEOUT = 30


def _log(msg: str) -> None:
    print(f"[sfjazz] {msg}", flush=True)


def matches(url: str) -> bool:
    return "sfjazz.org" in url


def _parse_start(date_str: str | None, time_str: str | None) -> datetime | None:
    """Build a UTC start from the API's display date + time (Pacific wall-clock).

    We use `eventDateString` ("10/1/2026") + `eventTimeString` ("9:30 PM") rather
    than `eventDate`, whose tz offset is wrong (-05:00)."""
    if not (date_str and time_str):
        return None
    try:
        naive = datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %I:%M %p")
    except ValueError:
        return None
    return naive.replace(tzinfo=SOURCE_TZ).astimezone(timezone.utc)


def _abs_url(path: str | None) -> str | None:
    return urljoin(BASE_URL, path) if path else None


def parse_events(items: list[dict]) -> list[RawEvent]:
    """Map ace-api event objects to RawEvents (one per performance). Pure."""
    events: list[RawEvent] = []
    for it in items or []:
        title = (it.get("name") or "").strip()
        start_time = _parse_start(it.get("eventDateString"), it.get("eventTimeString"))
        if not (title and start_time):
            continue
        room = (it.get("location") or "").strip()
        location = f"SFJAZZ Center — {room}" if room else VENUE
        synopsis = (it.get("synopsis") or "").strip() or None
        events.append(RawEvent(
            title=title,
            start_time=start_time,
            location=location,
            url=_abs_url(it.get("viewDetailCtaUrl")),
            description=synopsis,
            image_url=_abs_url(it.get("thumbnail")),
        ))
    return events


def scrape(url: str = ACE_API, horizon: date | None = None) -> list[RawEvent]:
    """Fetch the full season from the Adage ace-api in one call and parse it."""
    today = date.today()
    end = horizon or (today + timedelta(days=LOOKAHEAD_DAYS))
    params = {"startDate": today.isoformat(), "endDate": end.isoformat()}
    try:
        resp = requests.get(ACE_API, params=params,
                            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
                            timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        items = resp.json()
    except (requests.RequestException, ValueError) as e:
        _log(f"ace-api fetch failed: {type(e).__name__}: {e}")
        return []
    events = parse_events(items if isinstance(items, list) else [])
    _log(f"done: {len(events)} events from ace-api")
    return events
