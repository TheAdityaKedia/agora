"""El Rio — Mission District bar/venue in San Francisco.

El Rio (elriosf.com) is a Squarespace site whose events calendar is an embedded
**Tockify** calendar (calname `elriosf2`, surfaced at /home#calendar). Squarespace
itself carries no event data — the page is a thin shell around Tockify's widget.

Data source (spiked): Tockify's agenda API,
`https://tockify.com/api/ngevent?calname=elriosf2&view=agenda&startms=<now-ms>&max=N`.
It returns clean JSON — one entry per **occurrence** (recurring nights like the
weekly Karaokiki are already expanded), each with:
  - `content.summary.text`  → title
  - `content.description.text` → HTML blurb (Squarespace-style `<p><span>` +
    a leading `<tkfmedia>` featured-image tag we strip)
  - `when.start.millis`    → absolute UTC epoch ms (tzid is display-only)
  - `eid.uid` / `eid.tid`  → build the detail URL (the info page)
  - `content.imageSets[0]` → poster image (square_272x272, `variantFormat` ext)

Every event on the calendar is public programming at the single venue (live
music, DJ/dance nights, karaoke, drag, comedy, movie nights) in SF — private
rentals live on a separate /private-events page, not the calendar — so no
content filtering is needed. Location is hardcoded to the venue.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "elriosf.com"
NAME = "El Rio"

HOME_URL = "https://www.elriosf.com/"
TOCKIFY_CALNAME = "elriosf2"
API_URL = "https://tockify.com/api/ngevent"
# Tockify detail (info) page for an occurrence: /<calname>/detail/<uid>/<tid>.
DETAIL_URL = "https://tockify.com/{cal}/detail/{uid}/{tid}"
# Tockify image CDN; the calendar only pre-generates the 272px square variant.
IMAGE_URL = "https://d3flpus5evl89n.cloudfront.net/{owner}/{img}/square_272x272.{ext}"

# El Rio is a single venue; the API carries no per-event location.
LOCATION = "El Rio, 3158 Mission St, San Francisco, CA"

REQUEST_TIMEOUT = 25
# One call with a generous cap pulls the whole forward calendar (~60 events);
# metaData.hasNext is respected as a safety check when walking further.
MAX_EVENTS = 500


def _log(msg: str) -> None:
    print(f"[elrio] {msg}", flush=True)


def matches(url: str) -> bool:
    return "elriosf.com" in url or TOCKIFY_CALNAME in url


def _clean_description(html: Optional[str]) -> Optional[str]:
    """Flatten Tockify's HTML blurb to plain text.

    Drops the leading `<tkfmedia>` featured-image tag and any social-link
    anchors (whose text is just "Instagram"/"Facebook"), decodes HTML entities,
    and collapses whitespace. Returns None when there's nothing left.
    """
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for media in soup.find_all("tkfmedia"):
        media.decompose()
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if any(s in href for s in ("instagram.com", "facebook.com", "twitter.com",
                                    "x.com", "tiktok.com")):
            a.decompose()
    text = soup.get_text(" ", strip=True)
    text = " ".join(text.split())
    return text or None


def _image_url(content: dict) -> Optional[str]:
    sets = content.get("imageSets")
    if not isinstance(sets, list) or not sets:
        return None
    img = sets[0]
    owner, iid = img.get("ownerId"), img.get("id")
    if not (owner and iid):
        return None
    # The pre-generated square variant is served in `variantFormat` (a png
    # master is still delivered as jpg), so use that extension, not masterFormat.
    ext = img.get("variantFormat") or img.get("masterFormat") or "jpg"
    return IMAGE_URL.format(owner=owner, img=iid, ext=ext)


def _event_from_entry(entry: dict) -> Optional[RawEvent]:
    if not isinstance(entry, dict):
        return None
    content = entry.get("content") or {}
    title = ((content.get("summary") or {}).get("text") or "").strip()
    start = ((entry.get("when") or {}).get("start") or {}).get("millis")
    if not title or not isinstance(start, (int, float)):
        return None
    start_time = datetime.fromtimestamp(start / 1000, tz=timezone.utc)

    eid = entry.get("eid") or {}
    uid, tid = eid.get("uid"), eid.get("tid")
    url = (DETAIL_URL.format(cal=TOCKIFY_CALNAME, uid=uid, tid=tid)
           if uid is not None and tid is not None else HOME_URL)

    return RawEvent(
        title=title,
        start_time=start_time,
        location=LOCATION,
        url=url,
        description=_clean_description((content.get("description") or {}).get("text")),
        image_url=_image_url(content),
    )


def parse_events(json_text: str) -> list[RawEvent]:
    """Parse a Tockify `/api/ngevent` JSON payload into RawEvents. Pure."""
    try:
        data = json.loads(json_text)
    except (ValueError, TypeError):
        return []
    entries = data.get("events") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    return [ev for ev in (_event_from_entry(e) for e in entries) if ev is not None]


def _fetch(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA,
                                      "Accept": "application/json"},
                        timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _api_url(now_ms: int, offset: int = 0) -> str:
    return (f"{API_URL}?max={MAX_EVENTS}&view=agenda&calname={TOCKIFY_CALNAME}"
            f"&start-inclusive=true&longForm=true&showAll=false"
            f"&startms={now_ms}&offset={offset}")


def scrape(url: str = HOME_URL, now=None,
           fetch: Optional[Callable[[str], str]] = None) -> list[RawEvent]:
    """Fetch El Rio's Tockify agenda and return one RawEvent per occurrence.

    `url` is accepted for dispatch symmetry (it's the elriosf.com listing); the
    event data always comes from the Tockify API. `now`/`fetch` are injectable
    for tests.
    """
    fetch = fetch or _fetch
    now_ms = int((now or datetime.now(timezone.utc)).timestamp() * 1000)
    api_url = _api_url(now_ms)
    _log(f"fetching Tockify agenda ({TOCKIFY_CALNAME}) from {now_ms}")
    try:
        payload = fetch(api_url)
    except requests.RequestException as e:
        _log(f"agenda fetch failed: {type(e).__name__}: {e}")
        return []
    events = parse_events(payload)
    _log(f"done: {len(events)} events")
    return events
