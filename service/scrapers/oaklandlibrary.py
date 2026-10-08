"""Oakland Public Library events scraper (BiblioCommons events API).

OPL's 18 branches plus the Main Library and the African American Museum &
Library run talks, book clubs, film screenings, music, art and maker
programs. Events live in BiblioCommons, whose public gateway API
(``gateway.bibliocommons.com/v2/libraries/oaklandlibrary/events``) returns
pages of events plus an ``entities`` block of branches (with street
addresses), audiences, types and images. It ignores date filters and isn't
sorted, so we read every page (~30 at 100) and let the pipeline's lookahead
trim far-future repeats.

Dropped, as for SFPL (scrapers/sfpl.py): cancelled events; kids' programs;
service or admin types (storytime, tech support, housing/social services,
Library Commission, Friends & volunteers); and by-appointment help desks
named only in the title ("Drop-In Computer Help", "Neighborhood Business
Assistance (by appointment only)"). Kids' programs are stricter than SFPL's:
OPL tags most children's series "Kids + Families" (MOCHA art, Tween Club:
~150 sessions), so an event with a kids' audience stays only when it also
names Adults, Teens or Seniors. Images: an event's own photo only; the stock
per-type artwork BiblioCommons substitutes is skipped.
"""
from __future__ import annotations

import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "oaklandlibrary.org"
NAME = "Oakland Public Library"
API_URL = "https://gateway.bibliocommons.com/v2/libraries/oaklandlibrary/events"
EVENTS_URL = "https://oaklandlibrary.org/events/"
EVENT_URL = "https://oaklandlibrary.bibliocommons.com/events/{id}"
FALLBACK_LOCATION = "Oakland Main Library, 125 14th St, Oakland, CA 94612"
PER_PAGE = 100
MAX_PAGES = 60
REQUEST_TIMEOUT = 25

KID_AUDIENCES = frozenset({"kids", "birth to 5", "grade schoolers", "tweens", "babies", "toddlers", "preschoolers"})
ADULT_AUDIENCES = frozenset({"adults", "teens", "seniors"})
SKIP_TYPES = frozenset({"storytime", "computer & technology support", "housing & social services",
                        "library commission", "friends & volunteers"})
SKIP_TITLE_PHRASES = ("computer help", "tech help", "homework help", "tax help", "by appointment",
                      "business assistance", "job help", "job search")
_UNIT_RE = re.compile(r",\s*(?:suite|ste\.?|unit|#|\d+(?:st|nd|rd|th) floor).*$", re.I)
_TRAILER_RE = re.compile(r"\s*Get accessibility information\.?\s*$", re.I)


def matches(url: str) -> bool:
    return "oaklandlibrary.org" in url


def location_for(loc: dict | None) -> str:
    """A branch record → "Oakland Public Library Rockridge Branch, 5366 College
    Avenue, Oakland, CA 94618". Main Library rooms (Children's Room, TeenZone)
    become rooms of the Main Library. Pure."""
    if not loc:
        return FALLBACK_LOCATION
    a = loc.get("address") or {}
    # "East 12th Street, Suite 271" / "14th Street, 2nd Floor": the map can't
    # place a unit, so keep only the street.
    street = " ".join(p for p in (a.get("number"), _UNIT_RE.sub("", a.get("street") or "")) if p).strip()
    tail = ", ".join(p for p in (street, a.get("city") or "Oakland", f"CA {a.get('zip', '')}".strip()) if p)
    name = (loc.get("name") or "").strip()
    if name == "Oakland History Center":
        label = "Oakland Main Library — Oakland History Center"  # 2nd floor of the Main Library
    elif name.startswith("Main ") and name != "Main Library":
        label = f"Oakland Main Library — {name[len('Main '):].removeprefix('Library ').strip()}"
    elif name == "Main Library":
        label = "Oakland Main Library"
    else:
        label = f"Oakland Public Library {name}" if name else "Oakland Public Library"
    return f"{label}, {tail}"


def _names(ids, table: dict) -> set[str]:
    return {(table.get(i) or {}).get("name", "").strip().lower() for i in ids or []} - {""}


def keep(definition: dict, entities: dict) -> bool:
    """False for cancelled, kid-only and service/admin events. Pure."""
    if definition.get("isCancelled"):
        return False
    audiences = _names(definition.get("audienceIds"), entities.get("eventAudiences", {}))
    if audiences & KID_AUDIENCES and not audiences & ADULT_AUDIENCES:
        return False
    types = _names(definition.get("typeIds"), entities.get("eventTypes", {}))
    if types & SKIP_TYPES:
        return False
    title = (definition.get("title") or "").lower()
    return not any(p in title for p in SKIP_TITLE_PHRASES)


def _description(raw: str | None) -> str | None:
    if not raw:
        return None
    text = re.sub(r"\s+", " ", BeautifulSoup(raw, "html.parser").get_text(" ")).strip()
    return _TRAILER_RE.sub("", text).strip() or None


def parse_page(data: dict) -> list[RawEvent]:
    """One API page (events + entities) → RawEvents. Pure."""
    entities = data.get("entities") or {}
    out = []
    for e in (entities.get("events") or {}).values():
        d = e.get("definition") or {}
        title = (d.get("title") or "").strip()
        try:
            start = datetime.fromisoformat((e.get("indexStart") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if not title or not keep(d, entities):
            continue
        image = (entities.get("images") or {}).get(d.get("featuredImageId")) or {}
        out.append(RawEvent(
            title=title,
            start_time=start,
            location=location_for((entities.get("locations") or {}).get(d.get("branchLocationId"))),
            url=EVENT_URL.format(id=e["id"]),
            description=_description(d.get("description")),
            image_url=image.get("url") if image.get("tag") == "Event" else None,
        ))
    return out


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    events: list[RawEvent] = []
    for page in range(1, MAX_PAGES + 1):
        r = session.get(API_URL, params={"limit": PER_PAGE, "page": page}, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        events.extend(parse_page(data))
        if page >= ((data.get("events") or {}).get("pagination") or {}).get("pages", 0):
            break
    print(f"[oaklandlibrary] {len(events)} events kept", flush=True)
    return events
