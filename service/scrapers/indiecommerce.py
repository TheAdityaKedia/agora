"""Shared helpers for IndieCommerce (ABA) bookstore sites.

Many independent bookstores run on the American Booksellers Association's
IndieCommerce platform (Drupal). Their events live at a month-scoped listing,
``/events/YYYY/MM``, of ``article.event-list`` cards (title, detail link, date),
and each detail page carries a schema.org ``Event`` in its JSON-LD ``@graph``
(ISO start time with offset) plus the full synopsis in
``.event-details__info--body``. Detail URLs are *not* always dated: stores
often give an event a custom alias (``/event/bunny``), so we take links from
the cards rather than pattern-matching hrefs.

Any such store plugs in with a thin per-source wrapper (see
scrapers/booksmith.py) supplying its site base and street address.
"""
from __future__ import annotations

import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SF_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 25
MAX_MONTHS = 13          # current month + a year of look-ahead
EMPTY_MONTHS_STOP = 3    # calendars thin out; stop after this many empty months in a row
DETAIL_WORKERS = 5

# Boilerplate lines stores append to every event body.
_NOISE_RE = re.compile(
    r"^(\*+|subscribe to our e-?newsletter|support our events program"
    r"|please contact .* with questions.*|if you are unable to attend.*)$",
    re.IGNORECASE,
)


@dataclass
class ListingItem:
    title: str
    url: str
    day: date | None


def month_path(d: date) -> str:
    return f"/events/{d.year}/{d.month:02d}"


def _parse_card_date(text: str) -> date | None:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if not m:
        return None
    month, day, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_listing(page_html: str, site_base: str) -> list[ListingItem]:
    """Event cards on one month page -> ListingItems. Pure."""
    soup = BeautifulSoup(page_html, "html.parser")
    items: list[ListingItem] = []
    for card in soup.select("article.event-list"):
        link = card.select_one(".event-list__title a[href]")
        if link is None:
            continue
        day = None
        for detail in card.select(".event-list__details--item"):
            label = detail.select_one(".event-list__details--label")
            if label and "Date" in label.get_text():
                day = _parse_card_date(detail.get_text(" ", strip=True))
        items.append(ListingItem(
            title=html.unescape(link.get_text(" ", strip=True)),
            url=urljoin(site_base, link["href"]),
            day=day,
        ))
    return items


def _json_ld_event(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
        for node in nodes or []:
            if isinstance(node, dict) and node.get("@type") == "Event":
                return node
    return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SF_TZ)
    return dt.astimezone(timezone.utc)


_BLOCK_TAGS = ["p", "li", "h1", "h2", "h3", "h4", "h5", "h6"]
_LOGISTICS_HEADING_RE = re.compile(r"^about this event:?$", re.IGNORECASE)
_SEPARATOR_RE = re.compile(r"^\*+$")


def _description(soup: BeautifulSoup) -> str | None:
    """The event body as text, synopsis first.

    Stores (Book Passage especially) open many bodies with an "ABOUT THIS
    EVENT" logistics block (store, seating, signing line, contact) set off by
    ``****`` separators. That's a poor lead for a listing, but it carries
    ticket prices the cost tagger uses, so it's moved to the end, not dropped.
    """
    body = soup.select_one(".event-details__info--body")
    if body is None:
        return None
    blocks = body.find_all(_BLOCK_TAGS) or [body]
    synopsis, logistics = [], []
    in_logistics = False
    for block in blocks:
        if block.find_parent(_BLOCK_TAGS) is not None:
            continue  # nested (a <p> inside an <li>): the outer block's text covers it
        text = re.sub(r"\s+", " ", block.get_text(" ", strip=True)).strip()
        if _LOGISTICS_HEADING_RE.match(text):
            in_logistics = True
            continue
        if _SEPARATOR_RE.match(text):
            in_logistics = False
            continue
        if text and not _NOISE_RE.match(text):
            (logistics if in_logistics else synopsis).append(text)
    lines = synopsis + ([f"Event details: {' '.join(logistics)}"] if logistics else [])
    return " ".join(lines) or None


def _location(soup: BeautifulSoup) -> str | None:
    place = soup.select_one(".event-details__location--location")
    if place is None:
        return None
    label = place.select_one(".event-list__details--label")
    if label:
        label.extract()
    text = re.sub(r"\s+", " ", place.get_text(" ", strip=True))
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r"\s*United States$", "", text).strip()
    return text or None


def parse_event(page_html: str, url: str, *, fallback_location: str | None = None) -> RawEvent | None:
    """One detail page -> RawEvent (None without a JSON-LD Event). Pure."""
    soup = BeautifulSoup(page_html, "html.parser")
    node = _json_ld_event(soup)
    if node is None:
        return None
    title = html.unescape(node.get("name") or "").strip()
    start_time = _parse_iso(node.get("startDate"))
    if not (title and start_time):
        return None
    og_image = soup.find("meta", property="og:image")
    return RawEvent(
        title=title,
        start_time=start_time,
        location=_location(soup) or fallback_location,
        url=url,
        description=_description(soup) or (node.get("description") or None),
        image_url=og_image.get("content") if og_image else None,
    )


def _get(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _add_month(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def scrape_events(site_base: str, *, fallback_location: str | None = None,
                  tag: str = "indiecommerce") -> list[RawEvent]:
    """Walk the month listings from this month forward, then enrich each
    upcoming card from its detail page (concurrently)."""
    site_base = site_base.rstrip("/")
    today = datetime.now(SF_TZ).date()
    month = today.replace(day=1)
    items: dict[str, ListingItem] = {}
    empty_run = 0
    for _ in range(MAX_MONTHS):
        path = month_path(month)
        try:
            found = parse_listing(_get(site_base + path), site_base)
        except requests.RequestException as e:
            print(f"[{tag}] listing {path} failed: {e}", flush=True)
            break
        upcoming = [i for i in found if i.day is None or i.day >= today]
        print(f"[{tag}] {path}: {len(upcoming)} upcoming", flush=True)
        for item in upcoming:
            items.setdefault(item.url, item)
        empty_run = 0 if found else empty_run + 1
        if empty_run >= EMPTY_MONTHS_STOP:
            break
        month = _add_month(month)

    def fetch(item: ListingItem) -> RawEvent | None:
        try:
            return parse_event(_get(item.url), item.url, fallback_location=fallback_location)
        except requests.RequestException as e:
            print(f"[{tag}] detail failed {item.url}: {e}", flush=True)
            return None

    t0 = time.monotonic()
    print(f"[{tag}] fetching {len(items)} detail pages ({DETAIL_WORKERS} workers)", flush=True)
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        events = [e for e in pool.map(fetch, items.values()) if e is not None]
    print(f"[{tag}] done: {len(events)} events in {time.monotonic() - t0:.0f}s", flush=True)
    return events
