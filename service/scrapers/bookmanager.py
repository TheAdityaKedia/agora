"""Shared helpers for BookManager bookstore webstores.

BookManager (a bookstore POS + webstore platform) serves store sites as a JS
SPA backed by ``api.bookmanager.com``. Events come from
``POST /customer/event/v2/list`` (multipart form: ``session_id``,
``store_id``, ``start_date=YYYYMMDD``, ``offset``, ``limit``); an anonymous
``session_id`` comes from ``POST /customer/session/get``. Each row has a local
``date`` + ``start_time``, an HTML ``description``/``summary``, an optional
``location_text``, and an ``image_url``. The SPA renders one event at
``<site>/events/<id>``. Any such store plugs in with a thin wrapper (see
scrapers/tallyho.py) supplying its store id and site base.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


API_BASE = "https://api.bookmanager.com/customer"
SF_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 25
PAGE_SIZE = 20
MAX_PAGES = 25  # safety bound


# Short call-to-action paragraphs ("Click here for tickets!") that stores put
# above the blurb; the link itself is gone once flattened to text.
_CTA_RE = re.compile(r"^(click here|tickets? here|get (your )?tickets|rsvp here)\b", re.IGNORECASE)
_CTA_MAX_CHARS = 60


def _clean_html(raw: str | None) -> str | None:
    if not raw:
        return None
    soup = BeautifulSoup(raw, "html.parser")
    for block in soup.find_all(["p", "div"]):
        text = block.get_text(" ", strip=True)
        if len(text) <= _CTA_MAX_CHARS and _CTA_RE.match(text):
            block.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    return text or None


def _parse_start(row: dict) -> datetime | None:
    day = row.get("date") or ""
    time = "00:00:00" if row.get("all_day") else (row.get("start_time") or "00:00:00")
    try:
        local = datetime.strptime(f"{day} {time}", "%Y%m%d %H:%M:%S")
    except ValueError:
        return None
    return local.replace(tzinfo=SF_TZ).astimezone(timezone.utc)


def parse_events(rows: list[dict], *, site_base: str,
                 fallback_location: str | None = None) -> list[RawEvent]:
    """Map event/v2/list rows to RawEvents. Pure."""
    events: list[RawEvent] = []
    for row in rows or []:
        title = (row.get("title") or "").strip()
        start_time = _parse_start(row)
        if not (title and start_time):
            continue
        events.append(RawEvent(
            title=title,
            start_time=start_time,
            location=(row.get("location_text") or "").strip() or fallback_location,
            url=f"{site_base.rstrip('/')}/events/{row['id']}" if row.get("id") else site_base,
            description=_clean_html(row.get("description")) or _clean_html(row.get("summary")),
            image_url=row.get("image_url") or None,
        ))
    return events


def _post(path: str, site_base: str, **fields) -> dict:
    resp = requests.post(
        f"{API_BASE}/{path}",
        files={k: (None, str(v)) for k, v in fields.items()},  # multipart form, as the SPA sends
        headers={"User-Agent": BROWSER_UA, "Origin": site_base, "Referer": site_base + "/"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def scrape_events(store_id: int, site_base: str, *, fallback_location: str | None = None,
                  tag: str = "bookmanager") -> list[RawEvent]:
    site_base = site_base.rstrip("/")
    try:
        session_id = _post("session/get", site_base, uuid=uuid.uuid4(), session_id="undefined",
                           store_id=store_id)["session_id"]
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"[{tag}] session fetch failed: {e}", flush=True)
        return []
    start_date = datetime.now(SF_TZ).strftime("%Y%m%d")
    rows: list[dict] = []
    for page in range(MAX_PAGES):
        try:
            data = _post("event/v2/list", site_base, session_id=session_id, store_id=store_id,
                         start_date=start_date, offset=page * PAGE_SIZE, limit=PAGE_SIZE)
        except (requests.RequestException, ValueError) as e:
            print(f"[{tag}] event list failed at offset {page * PAGE_SIZE}: {e}", flush=True)
            break
        batch = data.get("rows") or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
    events = parse_events(rows, site_base=site_base, fallback_location=fallback_location)
    print(f"[{tag}] {len(events)} events", flush=True)
    return events
