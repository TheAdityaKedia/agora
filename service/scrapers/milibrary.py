"""Mechanics' Institute (milibrary.org) events scraper.

The Mechanics' Institute — a downtown SF membership library + chess club —
runs author talks, Movies at Mechanics', Music at Mechanics', seminars and
reading groups. Its /events page is a custom CMS that inlines the whole
calendar (current + next month) as a JS object literal, ``var events = {"1":
[{…}], "2": […]}`` keyed by day of month, each entry carrying id, title,
local ``start_date``, ``short_description``, ``preview_image``, tag ids and a
``cancelled_at``. It's a JS literal with trailing commas, so we strip those
and json-decode it; no detail pages needed.

Skipped: chess tournaments (tag 972 — all-day competitions for registered
players, many per month) and online-only sessions ("(ONLINE)" in the title).
Event pages live at /events/<id>.
"""
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "milibrary.org"
NAME = "Mechanics' Institute"
BASE_URL = "https://www.milibrary.org"
EVENTS_URL = "https://www.milibrary.org/events"
ADDRESS = "Mechanics' Institute, 57 Post St, San Francisco, CA 94104"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 25
CHESS_TOURNAMENT_TAG = "972"

_EVENTS_RE = re.compile(r"var events = (\{.*?\});\s*\n", re.S)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def matches(url: str) -> bool:
    return "milibrary.org" in url


def _calendar(html: str) -> list[dict]:
    m = _EVENTS_RE.search(html)
    if not m:
        return []
    try:
        data = json.loads(_TRAILING_COMMA_RE.sub(r"\1", m.group(1)))
    except json.JSONDecodeError:
        return []
    return [e for day in data.values() if isinstance(day, list) for e in day if isinstance(e, dict)]


def _start(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value or "", "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=SOURCE_TZ).astimezone(timezone.utc)
    except ValueError:
        return None


def parse(html: str) -> list[RawEvent]:
    """The inlined calendar on /events → RawEvents. Pure."""
    events: list[RawEvent] = []
    for e in _calendar(html):
        title = (e.get("title") or "").strip()
        start = _start(e.get("start_date"))
        if not (title and start) or e.get("cancelled_at") or e.get("visibility") != "public":
            continue
        if CHESS_TOURNAMENT_TAG in (e.get("event_tag_ids") or "").split(","):
            continue
        if "(online)" in title.lower():
            continue
        image = e.get("preview_image") or None
        events.append(RawEvent(
            title=title,
            start_time=start,
            location=ADDRESS,
            url=f"{BASE_URL}/events/{e['id']}" if e.get("id") else EVENTS_URL,
            description=(e.get("short_description") or "").strip() or None,
            image_url=BASE_URL + quote(image) if image and image.startswith("/") else image,
        ))
    return events


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(EVENTS_URL, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    events = parse(resp.text)
    print(f"[milibrary] {len(events)} events", flush=True)
    return events
