"""The Faight Collective — events at 475 Haight St (Lower Haight).

thefaight.com/events is a Next.js page whose React Server Components payload
(`self.__next_f.push(...)` script chunks) embeds every published event as a
Sanity CMS object: title, UTC start/end, ticket URL (`ctaUrl`, usually
Eventbrite), description (portable-text blocks), poster asset, room, and an
`isPrivate` flag. We read those objects from the page — what the public page
publishes — and skip `isPrivate` ones (the page hides them too).

Not used: the Sanity dataset (3l1powkg/production) is publicly queryable, but
it returns internal booking records (drafts, Notion ids) that aren't
announced. Only the published page counts.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import requests

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA

SOURCE = "thefaight.com"
NAME = "The Faight Collective"
EVENTS_URL = "https://www.thefaight.com/events"
LOCATION = "The Faight Collective, 475 Haight St, San Francisco, CA 94117"
SANITY_IMAGE = "https://cdn.sanity.io/images/3l1powkg/production/{name}.{ext}"
REQUEST_TIMEOUT = 25
STARTED_GRACE = timedelta(hours=6)

_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
_ASSET_RE = re.compile(r"^image-([0-9a-f]+-\d+x\d+)-(\w+)$")


def _log(msg: str) -> None:
    print(f"[faight] {msg}", flush=True)


def matches(url: str) -> bool:
    return "thefaight.com" in url


def _payload(html: str) -> str:
    """The concatenated RSC payload strings from the page's script chunks."""
    parts = []
    for raw in _PUSH_RE.findall(html):
        try:
            parts.append(json.loads(f'"{raw}"'))
        except ValueError:
            continue
    return "".join(parts)


def _event_objects(html: str) -> list[dict]:
    payload, decoder = _payload(html), json.JSONDecoder()
    seen: dict[str, dict] = {}
    for m in re.finditer(r'\{"_id":', payload):
        try:
            obj, _ = decoder.raw_decode(payload, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("startTime") and obj.get("slug") and obj.get("title"):
            seen.setdefault(obj["_id"], obj)
    return list(seen.values())


def _text(blocks) -> str | None:
    """Portable-text blocks → plain text paragraphs."""
    paras = []
    for block in blocks or []:
        if isinstance(block, dict) and block.get("_type") == "block":
            text = "".join(c.get("text", "") for c in block.get("children") or [] if isinstance(c, dict))
            if text.strip():
                paras.append(text.strip())
    return "\n\n".join(paras) or None


def _image(poster) -> str | None:
    ref = ((poster or {}).get("asset") or {}).get("_ref") or ""
    m = _ASSET_RE.match(ref)
    return SANITY_IMAGE.format(name=m.group(1), ext=m.group(2)) if m else None


def _clean_url(url: str) -> str:
    """Drop tracking query params (?aff=…) from ticket links."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def parse(html: str, *, now: datetime | None = None) -> list[RawEvent]:
    now = now or datetime.now(timezone.utc)
    events = []
    for obj in _event_objects(html):
        if obj.get("isPrivate"):
            continue
        try:
            start = datetime.fromisoformat(obj["startTime"].replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            continue
        if start < now - STARTED_GRACE:
            continue
        slug = (obj.get("slug") or {}).get("current")
        url = _clean_url(obj["ctaUrl"]) if obj.get("ctaUrl") else (f"{EVENTS_URL}#{slug}" if slug else EVENTS_URL)
        events.append(RawEvent(
            title=obj["title"].strip(),
            start_time=start,
            location=LOCATION,
            url=url,
            description=_text(obj.get("description")),
            image_url=_image(obj.get("poster")),
        ))
    return sorted(events, key=lambda e: e.start_time)


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    events = parse(resp.text)
    _log(f"done: {len(events)} events")
    return events
