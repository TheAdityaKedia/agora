"""Great Star Theater events scraper.

The what's-playing listing is plain server-rendered HTML: `.event-list-item`
(an <a>) with:
  - h3 (title)
  - .event-time ("September 25 - 26, 2026" or "September 25, 2026")
  - .event-image img (may be site-relative)
  - .event-desc for the blurb

Each card is a *show/run*, not a single showing: a card's `.event-time` is
often a short range ("September 25 - 26, 2026", "October 8 - 25, 2026") that
hides several individual performances (sometimes two per day). The actual
showtimes live on the ticketing page the card links to — most Great Star events
are third-party and link to TicketTailor (either `tickettailor.com` or the
venue's white-labeled `tickets.greatstartheater.org`), with a handful going to
Eventbrite/Fever/etc.

TicketTailor renders one schema.org `Event` JSON-LD block *per occurrence*, each
with a tz-explicit `startDate` (no year inference needed). So `scrape()` renders
each card's ticketing page in a headless browser and emits one RawEvent per
occurrence (see `parse_performances`). TicketTailor throttles aggressively and
returns 403 intermittently even to a headless browser, and non-TicketTailor
pages expose no such JSON-LD; in either case the page yields no performances and
we fall back to a single run-level event (7 PM SF-local on the range's start
day) so a show is never dropped.
"""
import json
import re
import time
from datetime import date, datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import (
    BROWSER_UA, RateLimited, browser_context, load_page_html, new_browser_context,
)
from scrapers.performances import expand_shows


SOURCE = "greatstartheater.org"
NAME = "Great Star Theater"
BASE_URL = "https://www.greatstartheater.org"
EVENTS_URL = "https://www.greatstartheater.org/whats-playing"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "Great Star Theater, 636 Jackson St, San Francisco, CA 94133"
DEFAULT_HOUR = 20  # 8 PM — comedy/show default
REQUEST_TIMEOUT = 25

# TicketTailor is a client-rendered SPA; give it time to paint the JSON-LD.
PERF_WAIT_UNTIL = "load"
PERF_SETTLE_MS = 6000
# Pause before each ticketing-page render. Plain politeness: TicketTailor's
# robots.txt sets no crawl-delay, and this delay is NOT what gets us past the
# 403s. Those hit the same 7 pages (all under the `greatstartheater` TicketTailor
# account) on the first load in every run, delay or not, and a fresh browser
# context passes them. The retry in _scrape_show_performances is the fix.
PERF_DELAY_S = 5

_DASH_RE = re.compile(r"[–—-]")


def matches(url: str) -> bool:
    return "greatstartheater.org" in url


def _parse_full_date(text: str) -> date | None:
    text = " ".join(text.strip().split())
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_date_range(text: str) -> date | None:
    """Return the START date of a range like 'September 25 - 26, 2026' or a
    single date 'September 25, 2026'.

    The year always lives at the tail of the string. In a range the left side
    carries month + day but no year ('September 25'); the right side is usually
    day-only ('26') and unparseable on its own, so we build the start date from
    the left side plus the tail year.
    """
    text = _DASH_RE.sub("-", " ".join(text.strip().split()))
    # Single date first
    d = _parse_full_date(text)
    if d:
        return d
    # Range: split off the left (start) side and reattach the tail year.
    parts = [p.strip() for p in text.split("-", 1)]
    if len(parts) != 2:
        return None
    m = re.search(r"(\d{4})\s*$", text)
    if not m:
        return None
    year = m.group(1)
    # Left is normally 'September 25' (month + day, no year).
    left = _parse_full_date(f"{parts[0]}, {year}")
    if left:
        return left
    # Fallback: right side was a full date and left was day-only ('25') sharing
    # the right side's month.
    right = _parse_full_date(parts[1])
    if right:
        try:
            return date(right.year, right.month, int(parts[0]))
        except ValueError:
            return right
    return None


def _parse_event(el) -> RawEvent | None:
    title_tag = el.select_one("h3") or el.select_one(".event-list-item-content h3")
    if not title_tag:
        return None
    title = title_tag.get_text(strip=True)

    time_tag = el.select_one(".event-time") or el.select_one(".event-dates")
    if not time_tag:
        return None
    date_text = time_tag.get_text(" ", strip=True)
    start_day = _parse_date_range(date_text)
    if not start_day:
        return None

    href = el.get("href")

    img_tag = el.select_one("img.event-image") or el.find("img")
    image_url = None
    if img_tag and img_tag.get("src"):
        image_url = urljoin(BASE_URL, img_tag["src"])

    desc_tag = el.select_one(".event-desc")
    description = None
    if desc_tag:
        description = desc_tag.get_text(" ", strip=True)
    description = " · ".join(b for b in (date_text, description) if b)

    start_time = datetime(
        start_day.year, start_day.month, start_day.day, DEFAULT_HOUR, 0,
        tzinfo=SOURCE_TZ,
    ).astimezone(timezone.utc)

    return RawEvent(
        title=title,
        start_time=start_time,
        location=VENUE,
        url=href if href else None,
        description=description,
        image_url=image_url,
    )


def parse(html: str) -> list[RawEvent]:
    soup = BeautifulSoup(html, "html.parser")
    return [ev for ev in (_parse_event(el) for el in soup.select(".event-list-item")) if ev is not None]


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _event_ld_nodes(html: str) -> list[dict]:
    """Return every schema.org `Event` JSON-LD object on the page.

    TicketTailor emits one `Event` block per occurrence (each a top-level object,
    not a `subEvent[]`), so we collect them all.
    """
    soup = BeautifulSoup(html, "html.parser")
    nodes: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for obj in data if isinstance(data, list) else [data]:
            if isinstance(obj, dict) and obj.get("@type") == "Event":
                nodes.append(obj)
    return nodes


def parse_performances(html: str, *, show: RawEvent) -> list[RawEvent]:
    """Expand one show's ticketing page into one RawEvent per occurrence.

    TicketTailor renders each occurrence as its own schema.org `Event` JSON-LD
    object with a tz-explicit `startDate`. We keep the clean listing title,
    venue, description, and image from `show`, and give every occurrence the
    same stable show-level landing URL — `show.url`, the listing card's link.
    We deliberately do NOT use the per-occurrence `offers[].url` deep link: it
    varies per occurrence and often redirects to a white-labeled host
    (`tickets.greatstartheater.org/...?date_id=...`), whereas the card URL is
    one stable info page shared by all performances. Pages without such JSON-LD
    (Eventbrite/Fever/etc., or a 403 challenge) yield [] so the caller keeps the
    run-level event.
    """
    events: list[RawEvent] = []
    seen: set[str] = set()
    for node in _event_ld_nodes(html):
        raw_start = node.get("startDate")
        start = _parse_iso(raw_start)
        if not start:
            continue
        if raw_start in seen:
            continue
        seen.add(raw_start)
        events.append(RawEvent(
            title=show.title,
            start_time=start.astimezone(timezone.utc),
            location=show.location,
            url=show.url,
            description=show.description,
            image_url=show.image_url,
        ))
    return events


def _scrape_show_performances(ctx, show: RawEvent) -> list[RawEvent]:
    """Render one show's ticketing page and parse its occurrences.

    Returns [] when the show has no URL, the ticketing host blocks us, or the
    page exposes no per-occurrence JSON-LD — the caller falls back to the
    run-level event in that case.
    """
    if not show.url:
        return []
    time.sleep(PERF_DELAY_S)
    try:
        html = load_page_html(ctx, show.url, wait_until=PERF_WAIT_UNTIL, settle_ms=PERF_SETTLE_MS)
    except RateLimited:
        # Retry once on a fresh cookie jar (the same fix as greenapple.py); in
        # practice this clears every challenge. A second 403 falls back to the
        # run-level event.
        print(f"[greatstar] challenged at {show.url}; retrying once in a fresh browser context", flush=True)
        fresh = new_browser_context(ctx.browser)
        try:
            html = load_page_html(fresh, show.url, wait_until=PERF_WAIT_UNTIL, settle_ms=PERF_SETTLE_MS)
        except RateLimited as e:
            print(f"[greatstar] blocked (HTTP {e.status}) at {e.url}, skipping performances", flush=True)
            return []
        finally:
            fresh.close()
    return parse_performances(html, show=show)


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    """Fetch Great Star's listing, then expand each show to its performances.

    The listing is plain server-rendered HTML (one card per show/run). Each
    card's ticketing page (usually TicketTailor) is rendered in a headless
    browser and expanded into one event per occurrence; shows whose page can't
    be fetched or exposes no occurrences fall back to the run-level event.
    """
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[greatstar] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    shows = parse(resp.text)
    # full_chromium: consistent with greenapple.py; see browser_context().
    return expand_shows(shows, _scrape_show_performances, label="greatstar",
                        _browser=lambda: browser_context(full_chromium=True))
