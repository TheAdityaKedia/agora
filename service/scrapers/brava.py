"""Brava Theater Center events scraper.

Squarespace Summary block: `.summary-item-record-type-event` cards with
`.summary-title-link`, `time.summary-metadata-item--date` ("September 17,
2026 – September 19, 2026" or single "September 20, 2026"), and a
`img.summary-thumbnail-image`. `parse()` emits one run-level RawEvent per card
at the range's start day at 7:30 PM SF-local, with the full range in the
description.

Per performance: Squarespace records each event as a *single* schema.org
`Event` on its detail page (one `startDate`/`endDate`), and its ICS feed
(`?format=ical`) returns a single `VEVENT` — there is no per-day `subEvent`
list and no per-occurrence ICS breakdown. So a multi-day run genuinely cannot
be split into separate showings from anything the source exposes (and blindly
expanding a range into daily events would invent shows that never happen — a
month-long run does not play nightly). What the detail page *does* add is the
*actual* start time and venue, versus the listing's 7:30 PM placeholder. So
`scrape()` fetches each show's detail page and emits one performance at that
real datetime (this makes the majority single-date cards true, accurately-timed
per-performance rows), falling back to the run-level listing event when the
detail page carries no `Event` JSON-LD.
"""
import json
import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA, RateLimited, load_page_html
from scrapers.performances import expand_shows


SOURCE = "brava.org"
NAME = "Brava Theater Center"
BASE_URL = "https://www.brava.org"
EVENTS_URL = "https://www.brava.org/events"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "Brava Theater Center, 2781 24th St, San Francisco, CA 94110"
DEFAULT_HOUR = 19
DEFAULT_MINUTE = 30
REQUEST_TIMEOUT = 25

# Detail pages are server-rendered (the Event JSON-LD is in the initial HTML),
# so "load" plus a short settle is plenty.
DETAIL_WAIT_UNTIL = "load"
DETAIL_SETTLE_MS = 1500

_DASH_RE = re.compile(r"[–—-]")


def matches(url: str) -> bool:
    return "brava.org" in url


def _parse_full_date(text: str) -> date | None:
    """Parse 'September 17, 2026'."""
    text = " ".join(text.strip().split())
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_date_range(text: str) -> tuple[date | None, date | None]:
    normalized = _DASH_RE.sub("-", " ".join(text.strip().split()))
    parts = [p.strip() for p in normalized.split("-", 1)]
    left = _parse_full_date(parts[0])
    right = _parse_full_date(parts[1]) if len(parts) == 2 else None
    return left, right


def _parse_event(card) -> RawEvent | None:
    title_a = card.select_one(".summary-title-link") or card.select_one(".summary-title a")
    if not title_a:
        return None
    title = title_a.get_text(strip=True)
    href = title_a.get("href")

    date_tag = card.select_one("time.summary-metadata-item--date")
    if not date_tag:
        return None
    date_text = date_tag.get_text(" ", strip=True)
    start_day, end_day = _parse_date_range(date_text)
    if not start_day:
        return None

    img = card.select_one("img.summary-thumbnail-image") or card.find("img")
    image_url = None
    if img:
        image_url = img.get("data-image") or img.get("data-src") or img.get("src")

    excerpt_tag = card.select_one(".summary-excerpt")
    excerpt = excerpt_tag.get_text(" ", strip=True) if excerpt_tag else None
    cat_tag = card.select_one(".summary-metadata-item--cats")
    category = cat_tag.get_text(" ", strip=True) if cat_tag else None
    description = " · ".join(b for b in (category, date_text, excerpt) if b) or None

    start_time = datetime(
        start_day.year, start_day.month, start_day.day, DEFAULT_HOUR, DEFAULT_MINUTE,
        tzinfo=SOURCE_TZ,
    ).astimezone(timezone.utc)

    return RawEvent(
        title=title,
        start_time=start_time,
        location=VENUE,
        url=urljoin(BASE_URL, href) if href else None,
        description=description,
        image_url=image_url,
    )


def parse(html: str) -> list[RawEvent]:
    soup = BeautifulSoup(html, "html.parser")
    return [ev for ev in (_parse_event(c) for c in soup.select(".summary-item-record-type-event")) if ev is not None]


def _find_event_ld(html: str) -> dict | None:
    """Return the detail page's schema.org `Event` JSON-LD object, or None.

    Squarespace emits several JSON-LD blocks (WebSite, LocalBusiness, …); the
    one describing the showing is `@type == "Event"`.
    """
    soup = BeautifulSoup(html, "html.parser")
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
                return obj
    return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _location_str(event: dict) -> str | None:
    """Flatten the Squarespace Event `location` into a one-line venue string.

    Squarespace uses `location.address` as a single newline-joined string
    ("2781 24th Street\\nSan Francisco, CA, 94110\\nUnited States"), not the
    nested PostalAddress object other sources use.
    """
    loc = event.get("location") or {}
    if not isinstance(loc, dict):
        return None
    name = loc.get("name")
    addr = loc.get("address")
    parts = [name]
    if isinstance(addr, str):
        parts += [line.strip() for line in addr.splitlines() if line.strip()]
    elif isinstance(addr, dict):
        parts += [addr.get("streetAddress"), addr.get("addressLocality"), addr.get("postalCode")]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def parse_performances(html: str, *, show: RawEvent) -> list[RawEvent]:
    """Expand one Brava detail page into its performance(s).

    Squarespace stores each event as a single schema.org `Event` (one
    `startDate`), so this yields exactly one performance — but at the *actual*
    showtime and venue from the detail page rather than the listing's 7:30 PM
    placeholder. Returns [] when the page has no `Event` JSON-LD, so the caller
    falls back to the run-level listing event.
    """
    event = _find_event_ld(html)
    if not event:
        return []
    start = _parse_iso(event.get("startDate"))
    if not start:
        return []
    return [RawEvent(
        title=show.title,
        start_time=start.astimezone(timezone.utc),
        location=_location_str(event) or show.location,
        url=show.url,  # Brava has no per-performance ticket URL; keep the show page
        description=show.description,
        image_url=show.image_url,
    )]


def _scrape_show_performances(ctx, show: RawEvent) -> list[RawEvent]:
    """Render one show's detail page and parse its performance(s)."""
    if not show.url:
        return []
    try:
        html = load_page_html(ctx, show.url, wait_until=DETAIL_WAIT_UNTIL, settle_ms=DETAIL_SETTLE_MS)
    except RateLimited as e:
        print(f"[brava] blocked (HTTP {e.status}) at {e.url}, skipping performances", flush=True)
        return []
    return parse_performances(html, show=show)


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    """Fetch Brava's events listing, then expand each show to its performance(s).

    The listing is plain server-rendered HTML (one card per show). Each show's
    detail page carries a schema.org `Event` with the real showtime, which we
    render in a headless browser and emit as a single accurately-timed event.
    If a detail page can't be fetched or has no `Event` JSON-LD, we fall back to
    the run-level listing event so a show is never dropped.
    """
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[brava] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    shows = parse(resp.text)
    return expand_shows(shows, _scrape_show_performances, label="brava")
