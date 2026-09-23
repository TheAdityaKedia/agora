"""Z Space (San Francisco) events scraper.

Z Space's own site is a Squarespace shell with no machine-readable calendar, but
ticketing runs through OvationTix (AudienceView) client 34231, whose storefront
is a thin SPA over a clean JSON API. Two endpoints give us everything:

- ``CalendarProductions`` — one entry per date, each listing its productions and
  their individual ``showtimes`` (performanceId, start time, cancelled/visible
  flags). This is our per-performance source, so a multi-night run becomes one
  event per showing without any date fabrication.
- ``Production?expandPerformances=summary`` — the production catalog, keyed by
  id, carrying the rich HTML ``description`` and the ``venue`` name.

We join the two on production id: the calendar drives which performances exist,
the catalog supplies the synopsis and venue. Both endpoints need only a
``clientId`` header, so no headless browser is required.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import re
import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "zspace.org"
NAME = "Z Space"
CLIENT_ID = "34231"
API_BASE = "https://web.ovationtix.com/trs/api/rest"
CALENDAR_URL = f"{API_BASE}/CalendarProductions"
PRODUCTIONS_URL = f"{API_BASE}/Production?expandPerformances=summary"
# Public storefront landing page for a production — the show/info page, shared
# by every performance (start_time keeps each performance a distinct row).
PRODUCTION_URL = "https://ci.ovationtix.com/34231/production/{id}"
# Logo images are served from the same API by their numeric file id.
IMAGE_URL = f"{API_BASE}/ClientFile({{file}})"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 30

# The rich HTML shatters into many tiny inline fragments (each <b>/<a> is its
# own node), so line-level filtering is hopeless — we flatten to one string and
# truncate at the first schedule/logistics section header, which reliably marks
# the end of the synopsis. Everything after (dates, runtime, policies) is dropped.
_CUT_RE = re.compile(
    r"\b(schedule|preview[s]?|opening night|performances|show ?times"
    r"|run ?time|running time|age recommendation[s]?|content (notice|warning|advisory)"
    r"|accessibility|ticketing policies|box office|please note|dates?/?times?)\b",
    re.IGNORECASE,
)


def matches(url: str) -> bool:
    return "zspace.org" in url


def _clean_description(html: str | None, presenter: str | None) -> str | None:
    """Flatten the production HTML, cut trailing schedule/logistics blocks, and
    prepend the presenter label when the synopsis doesn't already state it."""
    text = ""
    if html:
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        text = re.sub(r"[﻿​\xa0]", " ", text)  # BOM / zero-width / nbsp
        text = re.sub(r"\s+", " ", text).strip()
        cut = _CUT_RE.search(text)
        if cut:
            text = text[:cut.start()].strip()
    presenter = (presenter or "").strip()
    if presenter and presenter.lower() not in text.lower():
        text = f"{presenter} · {text}" if text else presenter
    return text or None


def _parse_start(value: str | None) -> datetime | None:
    """Parse an OvationTix 'YYYY-MM-DD HH:MM' wall-clock time (SF local) to UTC."""
    if not value:
        return None
    try:
        naive = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=SOURCE_TZ).astimezone(timezone.utc)


def _index_productions(productions: list[dict]) -> dict[int, dict]:
    """Map production id -> {'venue', 'description', 'supertitle'} from the catalog."""
    index: dict[int, dict] = {}
    for p in productions or []:
        venue = p.get("venue") or {}
        index[p.get("id")] = {
            "venue": (venue.get("name") or "").strip() or None,
            "description": p.get("description"),
            "supertitle": p.get("supertitle"),
        }
    return index


def parse_events(calendar: list[dict], productions: list[dict]) -> list[RawEvent]:
    """Join the calendar (performances) with the catalog (descriptions/venue).

    Pure — no network — so it's testable against captured API responses. One
    RawEvent per visible, non-cancelled showtime.
    """
    catalog = _index_productions(productions)
    events: list[RawEvent] = []
    for day in calendar or []:
        for prod in day.get("productions") or []:
            pid = prod.get("productionId")
            detail = catalog.get(pid, {})
            title = prod.get("name") or detail.get("supertitle")
            presenter = prod.get("supertitle") or detail.get("supertitle")
            description = _clean_description(detail.get("description"), presenter)
            location = detail.get("venue") or NAME
            url = PRODUCTION_URL.format(id=pid) if pid is not None else None
            logo = prod.get("logoFile")
            image_url = IMAGE_URL.format(file=logo) if logo else None
            for st in prod.get("showtimes") or []:
                if st.get("isCancelled") or st.get("isVisible") is False:
                    continue
                start_time = _parse_start(st.get("performanceStartTime"))
                if not (title and start_time):
                    continue
                events.append(RawEvent(
                    title=title,
                    start_time=start_time,
                    location=location,
                    url=url,
                    description=description,
                    image_url=image_url,
                ))
    return events


def _fetch(url: str) -> list:
    resp = requests.get(
        url,
        headers={"clientId": CLIENT_ID, "Accept": "application/json", "User-Agent": BROWSER_UA},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    """Fetch the OvationTix calendar + production catalog and join them."""
    try:
        calendar = _fetch(CALENDAR_URL)
        productions = _fetch(PRODUCTIONS_URL)
    except (requests.RequestException, ValueError) as e:
        print(f"[zspace] API fetch failed: {e}", flush=True)
        return []
    events = parse_events(calendar, productions)
    print(f"[zspace] done: {len(events)} events from {len(calendar)} calendar days", flush=True)
    return events
