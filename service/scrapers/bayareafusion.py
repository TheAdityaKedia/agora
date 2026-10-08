"""Bay Area Fusion Calendar (bayareafusioncal.com) scraper.

A community calendar of fusion partner-dance socials, classes, workshops and
festivals. The site is a single-page app over Supabase: an ``events`` table
read with the public anon key that ships in the page's JS bundle (we read the
key and project URL from the live bundle at run time rather than committing
them, so a key rotation doesn't break us). Rows are schedules, not dated
occurrences, so we expand them with the app's own rule (``Ix`` in its bundle):

  - never before the row's ``created_at`` date;
  - a date in ``only_dates`` happens;
  - otherwise only ``recurring`` rows: the weekday is in ``days_of_week``
    (0 = Sunday) and the week of the month, ceil(day/7), in ``weeks_of_month``;
  - ``skip_dates`` remove a date;

and apply ``event_unique_occurrences`` (per-date title/time/venue overrides).
Times are free text ("8pm - 2am", "6:00 - 11:45pm", " - "); a start without
am/pm borrows the end's, and a bare hour defaults to pm (evening dances);
no time at all → noon (festival days). Rows outside the Bay Area
(Sacramento, Nevada City, Mendocino, Santa Cruz…) are dropped by county.
Event links go to the site's own page for that date.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from config import LOOKAHEAD_DAYS
from places.regions import REGION_OF_COUNTY, city_in_text
from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "bayareafusioncal.com"
NAME = "Bay Area Fusion Calendar"
SITE_URL = "https://bayareafusioncal.com/"
EVENT_URL = "https://bayareafusioncal.com/event/{short_id}?date={day}"
SELECT = "*,event_unique_occurrences!event_unique_occurrences_event_id_fkey(*)"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 25
BAY_REGIONS_WITHOUT_CITY = {"SF", "East Bay"}  # the site's own regions; its North/South Bay reach Lake and Santa Cruz counties

_BUNDLE_RE = re.compile(r'src="(/assets/index-[^"]+\.js)"')
_SUPABASE_URL_RE = re.compile(r"https://[a-z0-9]+\.supabase\.co")
_ANON_KEY_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_CLOCK_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m?\b", re.I)
_BARE_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?")


def matches(url: str) -> bool:
    return "bayareafusioncal.com" in url


def happens_on(row: dict, day: str) -> bool:
    """The app's own occurrence rule, for an ISO date string. Pure."""
    created = (row.get("created_at") or "")[:10]
    if created and day < created:
        return False
    if day in (row.get("skip_dates") or []):
        return False
    if day in (row.get("only_dates") or []):
        return True
    if row.get("schedule_type") and row["schedule_type"] != "recurring":
        return False
    d = date.fromisoformat(day)
    weekday = (d.weekday() + 1) % 7  # JS getUTCDay: 0 = Sunday
    week = math.ceil(d.day / 7)
    return weekday in (row.get("days_of_week") or []) and week in (row.get("weeks_of_month") or [])


def parse_start_time(text: str | None) -> tuple[int, int]:
    """Free-text "7:00 PM - 11:30 PM" → (19, 0). Start without am/pm borrows
    the end's; a bare hour is pm; nothing usable → noon. Pure."""
    text = text or ""
    start, _, end = text.partition("-")
    m = _CLOCK_RE.search(start)
    if m:
        hour, minute, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
    else:
        b = _BARE_RE.search(start)
        if not b:
            return 12, 0
        hour, minute = int(b.group(1)), int(b.group(2) or 0)
        e = _CLOCK_RE.search(end)
        ap = e.group(3).lower() if e else "p"
        if e and ap == "a" and hour > int(e.group(1)) and hour != 12:
            ap = "p"  # "9 - 2am": the start is the evening before
    hour = hour % 12 + (12 if ap == "p" else 0)
    return hour, minute


def _location(row: dict) -> str | None:
    """"Venue, address, city, CA" when the place is in a Bay Area county. Pure."""
    city = (row.get("city") or "").strip()
    parts = [p.strip() for p in (row.get("venue"), row.get("address"), city) if p and p.strip()]
    text = ", ".join(parts + ["CA"])
    found = city_in_text(f"{city}, CA") or city_in_text(text)
    if found:
        return text if REGION_OF_COUNTY.get(found[1]) else None
    return text if not city and row.get("region") in BAY_REGIONS_WITHOUT_CITY else None


def _description(row: dict) -> str | None:
    raw = row.get("description")
    text = BeautifulSoup(raw, "html.parser").get_text(" ") if raw else ""
    text = re.sub(r"\s+", " ", text).strip()
    extras = [f"Price: {row['price']}" for _ in [0] if row.get("price")]
    return " ".join([text] + extras).strip() or None


def expand(rows: list[dict], start: date, days: int) -> list[RawEvent]:
    """Schedule rows → one RawEvent per occurrence in [start, start+days). Pure."""
    out = []
    for row in rows:
        overrides = {o["date"]: o for o in row.get("event_unique_occurrences") or [] if o.get("date")}
        for n in range(days):
            day = (start + timedelta(days=n)).isoformat()
            if not happens_on(row, day):
                continue
            occ = {**row, **{k: v for k, v in overrides.get(day, {}).items() if v not in (None, "", []) and k != "id"}}
            location = _location(occ)
            title = (occ.get("title") or "").strip()
            if not (location and title):
                continue
            hour, minute = parse_start_time(occ.get("time"))
            local = datetime.combine(date.fromisoformat(day), datetime.min.time()).replace(
                hour=hour, minute=minute, tzinfo=SOURCE_TZ)
            out.append(RawEvent(
                title=title,
                start_time=local.astimezone(ZoneInfo("UTC")),
                location=location,
                url=EVENT_URL.format(short_id=row.get("short_id") or row["id"], day=day),
                description=_description(occ),
                image_url=occ.get("banner") or None,
            ))
    return out


def _credentials(session: requests.Session) -> tuple[str, str]:
    """The Supabase project URL and public anon key from the live JS bundle."""
    page = session.get(SITE_URL, timeout=REQUEST_TIMEOUT).text
    bundle = session.get(SITE_URL.rstrip("/") + _BUNDLE_RE.search(page).group(1), timeout=REQUEST_TIMEOUT).text
    return _SUPABASE_URL_RE.search(bundle).group(0), _ANON_KEY_RE.search(bundle).group(0)


def scrape(url: str = SITE_URL) -> list[RawEvent]:
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    base, key = _credentials(session)
    r = session.get(f"{base}/rest/v1/events", params={"select": SELECT, "approval_status": "eq.approved"},
                    headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    events = expand(r.json(), datetime.now(SOURCE_TZ).date(), LOOKAHEAD_DAYS)
    print(f"[bayareafusion] {len(events)} occurrences", flush=True)
    return events
