"""Masala Comedy Club (desi / Hinglish stand-up, South Bay) — via Tugoz.

masalacc.org/tickets just links to one page per running series (the main
show, e.g. "Laugh Ticket 10", and the bi-weekly open mic). Each page embeds a
Tugoz ticket widget whose event id lives in the site's `config.js`
(`SITE_CONFIG.LIVE_EVENTS`, which the club edits when a new season starts), so:

  1. config.js → the live Tugoz event ids;
  2. each id's Tugoz feed (scrapers/tugoz.py) → every upcoming show in that
     series, with time and venue;
  3. the series' page on masalacc.org (the feed's `eventurl`) → the blurb
     (meta description), the per-show comics lineup (rows link
     `?eid=<show id>`), and the price tiers from its JSON-LD offers.

The page's JSON-LD is maintained by hand and lags Tugoz, so it's only used for
prices; dates and venues come from Tugoz. Each show links to its series page
with `?eid=<show id>`, which opens the widget on that show.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from scrapers import tugoz
from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA

SOURCE = "masalacc.org"
NAME = "Masala Comedy Club"
SITE = "https://masalacc.org"
TICKETS_URL = f"{SITE}/tickets"
CONFIG_URL = f"{SITE}/config.js"
REQUEST_TIMEOUT = 25

_LIVE_EVENTS_RE = re.compile(r"LIVE_EVENTS\s*:\s*\{([^}]*)\}")
_EVENT_ID_RE = re.compile(r"\b\w+\s*:\s*(\d+)")
_EID_RE = re.compile(r"[?&]eid=(\d+)")


def _log(msg: str) -> None:
    print(f"[masala] {msg}", flush=True)


def matches(url: str) -> bool:
    return "masalacc.org" in url


def live_event_ids(config_js: str) -> list[int]:
    """Tugoz event ids from config.js's LIVE_EVENTS, in order."""
    m = _LIVE_EVENTS_RE.search(config_js)
    return [int(i) for i in _EVENT_ID_RE.findall(m.group(1))] if m else []


def parse_series_page(html: str) -> dict:
    """{'blurb', 'lineups': {show_id: comics}, 'prices': {utc start: [prices]}}."""
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"name": "description"})
    lineups = {}
    for link in soup.select("a.mcc-lineup__link[href]"):
        m = _EID_RE.search(link["href"])
        comics = link.select_one(".mcc-lineup__comics")
        if m and comics and comics.get_text(strip=True):
            lineups[int(m.group(1))] = comics.get_text(" ", strip=True)
    prices = {}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        for ev in data if isinstance(data, list) else [data]:
            try:
                start = datetime.fromisoformat(ev["startDate"]).astimezone(timezone.utc)
            except (KeyError, TypeError, ValueError):
                continue
            tiers = sorted({float(o["price"]) for o in ev.get("offers") or [] if o.get("price")})
            if tiers:
                prices[start] = tiers
    return {
        "blurb": (meta.get("content") or "").strip() if meta else "",
        "lineups": lineups,
        "prices": prices,
    }


def _price_line(tiers: list[float]) -> str:
    fmt = [f"${p:g}" for p in tiers]
    return "Tickets: " + (fmt[0] if len(fmt) == 1 else " / ".join(fmt))


def events_from_feed(feed: dict, page: dict, now: datetime | None = None) -> list[RawEvent]:
    info = feed.get("einfo") or {}
    page_url = (info.get("eventurl") or TICKETS_URL).rstrip("/")
    events = []
    for show in tugoz.upcoming_shows(feed, now):
        start = tugoz.show_start(show)
        parts = [page.get("blurb")]
        if show["eventid"] in page.get("lineups", {}):
            parts.append(f"Comics: {page['lineups'][show['eventid']]}")
        if start in page.get("prices", {}):
            parts.append(_price_line(page["prices"][start]))
        events.append(RawEvent(
            title=f"{NAME}: {tugoz.show_title(show)}",
            start_time=start,
            location=tugoz.show_location(show),
            url=f"{page_url}/?eid={show['eventid']}",
            description="\n\n".join(p for p in parts if p) or None,
            image_url=info.get("posterurl"),
        ))
    return events


def _get(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def scrape(url: str = TICKETS_URL) -> list[RawEvent]:
    ids = live_event_ids(_get(CONFIG_URL))
    if not ids:
        raise RuntimeError("no LIVE_EVENTS ids in masalacc.org/config.js (site changed?)")
    events = []
    for event_id in ids:
        feed = tugoz.fetch_feed(event_id)
        page = {}
        page_url = (feed.get("einfo") or {}).get("eventurl")
        if page_url:
            try:  # the page only adds lineups/prices; the feed alone is enough
                page = parse_series_page(_get(page_url))
            except Exception as e:
                _log(f"series page failed {page_url}: {type(e).__name__}: {e}")
        found = events_from_feed(feed, page)
        _log(f"tugoz {event_id}: {len(found)} upcoming shows")
        events.extend(found)
    return events
