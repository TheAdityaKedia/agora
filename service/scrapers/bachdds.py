"""Bach Dancing & Dynamite Society events scraper.

Bach DDS is a long-running jazz concert series at the Douglas Beach House on
Miramar Beach, Half Moon Bay. The site (bachddsoc.org) is WordPress but the
concert calendar is served by **VBO Tickets** (the same platform behind
`sfplayhouse.py`), embedded via a `connect.vbotickets.com` widget. There is no
Tribe/WordPress event API here.

Rather than the small "upcoming events" teaser widget (only the next handful of
shows), we go to VBO's full events list, reached with three plain `requests`
(no browser needed):

  1. `bachddsoc.org/calendar/` carries the VBO `SiteID` in an inline script.
  2. `plugin.vbotickets.com/plugin/loadplugin?siteid=<SiteID>&page=ListEvents`
     returns a bootstrap page that hands back the numeric `OrgID` and a fresh
     `userSessionID`.
  3. `plugin.vbotickets.com/Plugin/events/showevents?ViewType=list&o=<OrgID>&s=<session>`
     returns one card per event — title, category, poster, price, date/time,
     and a full synopsis (`.EventIntroText`).

Each in-person concert has a paired "LIVESTREAM" twin (same title/date, a
`Live Streaming` subcategory) we drop — it's the same show and an off-target
online duplicate for an in-person Bay Area calendar.

Dates come as `Sun, 9/27/2026 @ 4:30 PM` (full date + time, no inference
needed); we treat them as America/Los_Angeles and store UTC. Each event's `url`
is the show's info page on bachddsoc.org (`/calendar/?pg=selectevent&eid=<eid>`),
not the per-seat VBO checkout link.
"""
import html as _html
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "bachddsoc.org"
NAME = "Bach Dancing & Dynamite Society"
BASE_URL = "https://bachddsoc.org"
CALENDAR_URL = "https://bachddsoc.org/calendar/"
VBO_BASE = "https://plugin.vbotickets.com"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = ("Bach Dancing & Dynamite Society (Douglas Beach House), "
         "311 Mirada Rd, Half Moon Bay, CA 94019")
REQUEST_TIMEOUT = 25
# Fallback SiteID if the calendar page markup changes and we can't scrape it.
DEFAULT_SITE_ID = "CE358005-B38C-4936-B414-DF0B9C2F10B4"

_SITE_ID_RE = re.compile(r'SiteID\s*=\s*"([0-9A-Fa-f-]+)"')
_ORG_RE = re.compile(r'orgID\s*:\s*"(\d+)"')
_SESSION_RE = re.compile(r'value\s*:\s*"([0-9a-fA-F-]+)"')
_EID_RE = re.compile(r'\bEID(\d+)\b')
# "Sun, 9/27/2026 @ 4:30 PM"
_DATE_RE = re.compile(
    r'(?P<mon>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})\s*@\s*'
    r'(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>[AaPp][Mm])'
)
# Leading credit paragraphs and trailing link cruft in a synopsis.
_CREDIT_RE = re.compile(
    r'^(sponsored by\b|vibes (provided by|courtesy of)\b|presented by\b)',
    re.IGNORECASE,
)
_NOISE_RE = re.compile(
    r'^(artist website|video\s*\d|watch|listen|buy tickets)\b',
    re.IGNORECASE,
)


def _log(msg: str) -> None:
    print(f"[bachdds] {msg}", flush=True)


def matches(url: str) -> bool:
    return "bachddsoc.org" in url


def parse_site_id(html: str) -> str | None:
    """Return the VBO SiteID embedded in the bachddsoc calendar page, or None."""
    m = _SITE_ID_RE.search(html or "")
    return m.group(1) if m else None


def parse_session(html: str) -> tuple[str | None, str | None]:
    """Return (OrgID, userSessionID) from the VBO loadplugin bootstrap page."""
    org = _ORG_RE.search(html or "")
    sess = _SESSION_RE.search(html or "")
    return (org.group(1) if org else None, sess.group(1) if sess else None)


def _parse_datetime(text: str) -> datetime | None:
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    hour = int(m.group("hour")) % 12
    if m.group("ampm").lower() == "pm":
        hour += 12
    try:
        local = datetime(
            int(m.group("year")), int(m.group("mon")), int(m.group("day")),
            hour, int(m.group("minute")), tzinfo=SOURCE_TZ,
        )
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def _is_livestream(subcategory: str, title: str) -> bool:
    return "stream" in (subcategory or "").lower() or "LIVESTREAM" in title.upper()


def _clean_title(raw: str) -> str:
    return _html.unescape(" ".join((raw or "").split()))


def _extract_description(wrapper) -> str | None:
    """Return the synopsis from an event card's `.EventIntroText`, or None.

    The block is a run of `<p>` paragraphs: optional leading credit lines
    ("Sponsored by …", "Vibes provided by …"), the real bio, a personnel line,
    then link cruft ("Artist Website", "Video 1/2"). We drop the credits and the
    cruft, dedupe repeated paragraphs, and join the rest. Embedded tooltip
    `<script>` blocks are removed first so they never leak into the text.
    """
    block = wrapper.select_one(".EventIntroText")
    if block is None:
        return None
    for tag in block.select("script, style"):
        tag.decompose()
    paras: list[str] = []
    seen: set[str] = set()
    for p in block.find_all("p"):
        text = " ".join(p.get_text(" ", strip=True).split())
        if not text or _CREDIT_RE.match(text) or _NOISE_RE.match(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        paras.append(text)
    if not paras:
        return None
    return "\n\n".join(paras)


def _parse_event(wrapper) -> RawEvent | None:
    cls = " ".join(wrapper.get("class", []))
    m = _EID_RE.search(cls)
    if not m:
        return None
    eid = m.group(1)

    title = _clean_title(wrapper.get("data-event-name", ""))
    if not title:
        return None
    subcategory = wrapper.get("data-event-subcategory", "")
    if _is_livestream(subcategory, title):
        return None

    date_tag = wrapper.select_one(".TextEventDate")
    start_time = _parse_datetime(date_tag.get_text(" ", strip=True)) if date_tag else None
    if start_time is None:
        return None

    img = wrapper.select_one(".EventListPoster img")
    image_url = img.get("src") if img and img.get("src") else None

    return RawEvent(
        title=title,
        start_time=start_time,
        location=VENUE,
        url=f"{BASE_URL}/calendar/?pg=selectevent&eid={eid}&edid=0",
        description=_extract_description(wrapper),
        image_url=image_url,
    )


def parse_events(html: str) -> list[RawEvent]:
    """Parse a VBO `showevents` listing into in-person concert RawEvents.

    LIVESTREAM twins are dropped; each remaining card becomes one event.
    """
    soup = BeautifulSoup(html, "html.parser")
    events: list[RawEvent] = []
    for wrapper in soup.select(".EventListWrapper"):
        ev = _parse_event(wrapper)
        if ev is not None:
            events.append(ev)
    return events


def _fetch(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    """Resolve a VBO session for Bach DDS and parse its full events list."""
    _log(f"fetching calendar page {CALENDAR_URL} for SiteID")
    try:
        cal_html = _fetch(CALENDAR_URL)
        site_id = parse_site_id(cal_html) or DEFAULT_SITE_ID
    except Exception as e:  # noqa: BLE001 - be resilient, fall back to known id
        _log(f"calendar fetch failed ({type(e).__name__}: {e}); using default SiteID")
        site_id = DEFAULT_SITE_ID
    _log(f"SiteID={site_id}")

    loadplugin_url = f"{VBO_BASE}/plugin/loadplugin?siteid={site_id}&page=ListEvents"
    boot = _fetch(loadplugin_url)
    org_id, session = parse_session(boot)
    if not org_id or not session:
        _log("could not resolve OrgID/session from loadplugin; aborting")
        return []
    _log(f"OrgID={org_id} session={session}")
    time.sleep(0.5)

    showevents_url = (
        f"{VBO_BASE}/Plugin/events/showevents?ViewType=list&o={org_id}&s={session}"
    )
    _log(f"fetching events list {showevents_url}")
    listing = _fetch(showevents_url)
    events = parse_events(listing)
    described = sum(1 for e in events if e.description)
    _log(f"done: {len(events)} in-person events, {described} with descriptions")
    return events
