"""SF Bar Guide — recurring SF bar nights (trivia, karaoke, bingo, drag, comedy,
live music).

SF Bar Guide (sfbarguide.com) is a weekly-verified directory of recurring bar
events. Structure (spiked):
  - Homepage carries a JSON-LD `ItemList` of ~87 `BarOrPub` entries, each with a
    `/bar/<slug>` URL.
  - Each `/bar/<slug>` page carries a JSON-LD `BarOrPub` with an `event[]` array;
    every event has `name`, `description`, a next-occurrence `startDate`
    (tz-aware), and `eventSchedule.repeatFrequency` (e.g. "P1W").

We fetch the homepage, then each bar page concurrently, and expand every event's
recurrence into concrete dated occurrences within a rolling window (see
recurrence.py). Titles embed the venue so two bars' "Trivia Night" at the same
time stay distinct rows. See feature-specs/recurring-events.md.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA
from scrapers.recurrence import expand_occurrences, DEFAULT_HORIZON_DAYS

SOURCE = "sfbarguide.com"
NAME = "SF Bar Guide"
BASE_URL = "https://www.sfbarguide.com"
HOME_URL = "https://www.sfbarguide.com/"
REQUEST_TIMEOUT = 25
DETAIL_WORKERS = 6
DETAIL_LOG_EVERY = 20


def _log(msg: str) -> None:
    print(f"[sfbarguide] {msg}", flush=True)


def matches(url: str) -> bool:
    return "sfbarguide.com" in url


def _ld_blocks(html: str):
    soup = BeautifulSoup(html, "html.parser")
    for s in soup.find_all("script", type="application/ld+json"):
        raw = s.string
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue


def find_bar_urls(home_html: str) -> list[str]:
    """Bar-page URLs from the homepage's ItemList JSON-LD (first-seen order)."""
    for obj in _ld_blocks(home_html):
        if isinstance(obj, dict) and obj.get("@type") == "ItemList":
            urls: list[str] = []
            for it in obj.get("itemListElement") or []:
                url = (it.get("item") or {}).get("url") or it.get("url")
                if url and url not in urls:
                    urls.append(url)
            return urls
    return []


def _format_address(addr) -> str | None:
    if isinstance(addr, str):
        return addr
    if isinstance(addr, dict):
        parts = [addr.get("streetAddress"), addr.get("addressLocality"),
                 addr.get("addressRegion")]
        return ", ".join(p for p in parts if p) or None
    return None


def parse_bar_events(bar_html: str, now=None) -> list[RawEvent]:
    """Parse a bar page's BarOrPub JSON-LD into per-occurrence RawEvents."""
    bar = None
    for obj in _ld_blocks(bar_html):
        if isinstance(obj, dict) and obj.get("@type") == "BarOrPub":
            bar = obj
            break
    if bar is None:
        return []

    venue = bar.get("name") or "SF bar"
    venue_addr = _format_address(bar.get("address"))
    location = f"{venue}, {venue_addr}" if venue_addr else venue
    bar_url = bar.get("url")

    events: list[RawEvent] = []
    for ev in bar.get("event") or []:
        if not isinstance(ev, dict):
            continue
        name = ev.get("name")
        start = ev.get("startDate")
        if not (name and start):
            continue
        sched = ev.get("eventSchedule") or {}
        freq = sched.get("repeatFrequency")
        for occ in expand_occurrences(start, freq, DEFAULT_HORIZON_DAYS, now=now):
            events.append(RawEvent(
                # Venue in the title so (title, start_time) dedup stays unique
                # across bars running the same night at the same time.
                title=f"{name} at {venue}",
                start_time=occ,
                location=location,
                url=bar_url,
                description=ev.get("description"),
                image_url=None,
            ))
    return events


def _fetch(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def scrape(url: str = HOME_URL, now=None) -> list[RawEvent]:
    """Walk the homepage bar list, fetch each bar page concurrently, and expand
    every recurring event into dated occurrences."""
    _log(f"fetching homepage {url}")
    try:
        home = _fetch(url)
    except requests.RequestException as e:
        _log(f"homepage fetch failed: {type(e).__name__}: {e}")
        return []
    bar_urls = find_bar_urls(home)
    _log(f"{len(bar_urls)} bars; fetching pages ({DETAIL_WORKERS} workers)")

    events: list[RawEvent] = []
    done = 0
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        futures = {pool.submit(_fetch, u): u for u in bar_urls}
        for fut in as_completed(futures):
            done += 1
            bar_url = futures[fut]
            try:
                events.extend(parse_bar_events(fut.result(), now=now))
            except Exception as e:
                _log(f"bar {bar_url} failed: {type(e).__name__}: {e}")
            if done % DETAIL_LOG_EVERY == 0 or done == len(bar_urls):
                _log(f"{done}/{len(bar_urls)} bars parsed, {len(events)} occurrences")

    _log(f"done: {len(events)} occurrences from {len(bar_urls)} bars")
    return events
