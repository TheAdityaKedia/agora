"""Shared Veezi (veezi.com) cinema-ticketing scraping helpers.

Many independent Bay Area cinemas embed a Veezi "sessions" widget on their
site; the widget is a server-rendered page at
``https://ticketing.us.veezi.com/sessions/?siteToken=<token>`` that lists every
upcoming showtime. Any such venue plugs in with a thin per-source wrapper
(see scrapers/balboa.py and scrapers/fourstar.py) supplying only its site token.

Data source (top of the ladder): the sessions page embeds a schema.org
JSON-LD ``VisualArtsEvent[]`` — one entry per showtime with an absolute ISO
``startDate`` (tz-aware, no year inference), the film ``name``, the venue
address, and the per-session purchase ``url``. We read times/urls from that
array and map poster images by film title from the page's ``.film`` cards
(the JSON-LD carries no image). Veezi exposes no synopsis, so descriptions are
left empty — the film title, venue, and showtime are the useful signal.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


# The sessions page (and its relative poster URLs) live on the regional host.
SESSIONS_HOST = "https://ticketing.us.veezi.com"
SESSIONS_URL = f"{SESSIONS_HOST}/sessions/?siteToken={{token}}"
REQUEST_TIMEOUT = 25


def _log(msg: str) -> None:
    print(f"[veezi] {msg}", flush=True)


def _iter_json_ld(soup: BeautifulSoup):
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue


def _find_sessions(soup: BeautifulSoup) -> list[dict]:
    """Return the JSON-LD VisualArtsEvent list (the per-showtime array)."""
    for payload in _iter_json_ld(soup):
        if isinstance(payload, list) and any(
            isinstance(e, dict) and e.get("@type") == "VisualArtsEvent" for e in payload
        ):
            return [e for e in payload if isinstance(e, dict)]
    return []


def _poster_map(soup: BeautifulSoup) -> dict[str, str]:
    """Map film title -> absolute poster URL from the page's .film cards."""
    posters: dict[str, str] = {}
    for film in soup.select(".film"):
        title_el = film.select_one("h3.title")
        img = film.select_one("img.poster") or film.find("img")
        if not (title_el and img and img.get("src")):
            continue
        title = title_el.get_text(strip=True)
        if title and title not in posters:
            src = img["src"]
            posters[title] = src if src.startswith("http") else SESSIONS_HOST + src
    return posters


def _parse_start(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_sessions(html: str, *, fallback_location: str | None = None) -> list[RawEvent]:
    """Parse a Veezi sessions page into RawEvents (one per showtime).

    Pure — no network — so it's testable against a captured page.
    """
    soup = BeautifulSoup(html, "html.parser")
    posters = _poster_map(soup)
    events: list[RawEvent] = []
    for e in _find_sessions(soup):
        title = e.get("name")
        start_time = _parse_start(e.get("startDate"))
        if not (title and start_time):
            continue
        location = (e.get("location") or {}).get("address") or fallback_location
        events.append(RawEvent(
            title=title,
            start_time=start_time,
            location=location,
            url=e.get("url"),
            description=None,
            image_url=posters.get(title),
        ))
    return events


def scrape_sessions(site_token: str, *, fallback_location: str | None = None) -> list[RawEvent]:
    """Fetch and parse a venue's Veezi sessions page by its site token."""
    url = SESSIONS_URL.format(token=site_token)
    try:
        resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        _log(f"fetch failed for {site_token}: {exc}")
        return []
    events = parse_sessions(resp.text, fallback_location=fallback_location)
    _log(f"{site_token}: {len(events)} sessions")
    return events
