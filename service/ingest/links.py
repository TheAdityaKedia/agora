"""A submitted URL → events: known platforms (Momence, Partiful), then any
schema.org Event JSON-LD, then the LLM over the page's visible text."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from bs4 import BeautifulSoup

from ingest import LOCAL_TZ
from ingest.validate import check_event
import json

from scrapers import eventbrite, momence, partiful
from scrapers.base import RawEvent

MAX_PAGE_CHARS = 15_000
UNREADABLE = "couldn't read this page"
NO_EVENT = "no event found on this page"
_PARTIFUL_RE = re.compile(r"https?://(www\.)?partiful\.com/e/([A-Za-z0-9]+)")
_HAS_OFFSET_RE = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


@dataclass
class LinkResult:
    events: list[RawEvent] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    error: str | None = None


def _localize(obj: dict) -> dict:
    """JSON-LD startDate without an offset is local (Pacific) time."""
    start = obj.get("startDate") or ""
    if start and "T" in start and not _HAS_OFFSET_RE.search(start):
        try:
            naive = datetime.fromisoformat(start)
            obj = {**obj, "startDate": naive.replace(tzinfo=LOCAL_TZ).isoformat()}
        except ValueError:
            pass
    return obj


def _structured(url: str, html: str, now: datetime) -> LinkResult | None:
    obj = eventbrite.parse_event_page(html)
    if not obj:
        return None
    raw = eventbrite.event_from_json_ld(_localize(obj))
    if raw is None:
        return None
    place = obj.get("location")
    if isinstance(place, dict) and isinstance(place.get("address"), str):
        # eventbrite's formatter only reads structured addresses; generic pages
        # often give a plain string.
        raw.location = ", ".join(p for p in (place.get("name"), place["address"].strip()) if p)
    if "eventbrite." in url:
        raw.description = eventbrite.full_description(html) or raw.description
    raw.url = raw.url or url
    reason = check_event(raw, now=now)
    return LinkResult(error=reason) if reason else LinkResult(events=[raw])


def _page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return re.sub(r"\n\s*\n+", "\n\n", soup.get_text("\n")).strip()[:MAX_PAGE_CHARS]


_EVENTBRITE_RE = re.compile(r"https?://(www\.)?eventbrite\.[a-z.]+/e/")
_LUMA_RE = re.compile(r"https?://(www\.)?(lu\.ma|luma\.com)/[A-Za-z0-9_-]+/?$")


def is_known_platform(url: str) -> bool:
    """Links that resolve to an exact event without the LLM."""
    return bool(momence.session_id(url) or _PARTIFUL_RE.match(url)
                or _EVENTBRITE_RE.match(url) or _LUMA_RE.match(url))


def _momence(sid: str, *, fetch: Callable[[str], str], now: datetime) -> LinkResult:
    """Momence session pages are empty JS shells; their public JSON API isn't."""
    try:
        payload = json.loads(fetch(momence.session_api_url(sid)))
    except Exception:
        return LinkResult(error=UNREADABLE)
    raw = momence.event_from_session_detail(payload.get("message") or {})
    if raw is None:
        return LinkResult(error=NO_EVENT)
    reason = check_event(raw, now=now)
    return LinkResult(error=reason) if reason else LinkResult(events=[raw])


def resolve(url: str, *, fetch: Callable[[str], str],
            extract_text: Callable[[str, str], list[dict]], now: datetime) -> LinkResult:
    sid = momence.session_id(url)
    if sid:
        return _momence(sid, fetch=fetch, now=now)
    m = _PARTIFUL_RE.match(url)
    target = f"https://partiful.com/e/{m.group(2)}" if m else url
    try:
        html = fetch(target)
    except Exception:
        return LinkResult(error=UNREADABLE)
    if m:
        event, _, _ = partiful.parse_event_page(html)
        raw = partiful.to_raw_event(event, now=now, require_public=False) if event else None
        if raw is None:
            return LinkResult(error=NO_EVENT)
        reason = check_event(raw, now=now)
        return LinkResult(error=reason) if reason else LinkResult(events=[raw])
    structured = _structured(url, html, now)
    if structured is not None:
        return structured
    text = _page_text(html)
    candidates = extract_text(text, f"Page: {url}") if text else []
    for c in candidates:
        c["url"] = c.get("url") or url
    return LinkResult(candidates=candidates) if candidates else LinkResult(error=NO_EVENT)
