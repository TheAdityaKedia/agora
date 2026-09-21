"""Berkeley Repertory Theatre events scraper.

The what's-on listing (`/shows`) is plain server-rendered HTML — `.eventCard`
items expose title, a Fri/Mon-formatted date range, an absolute image URL, and
a relative show detail path. Each card is a *show*: a multi-week run, not a
single performance.

Individual showtimes live on each show's detail page, which embeds a
`<script type="application/ld+json">` block holding a *list* of schema.org
`Event` objects — one per performance — each with an absolute, tz-explicit
`startDate` (e.g. "2026-09-22T19:00:00-07:00", so no year inference is needed,
like ATG). Berkeley Rep exposes no per-performance ticket URL, so every showing
links back to the show detail page.

`scrape()` fetches the listing, then for each show renders the detail page and
emits one RawEvent per performance (see `parse_performances`). When a show has
no parseable performances (no detail URL, no Event JSON-LD, or a browser error)
we fall back to a single run-level event at 7:30 PM SF-local on the range's
start day, so a show is never dropped.
"""
import json
from datetime import date, datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA, RateLimited, load_page_html
from scrapers.performances import expand_shows


SOURCE = "berkeleyrep.org"
NAME = "Berkeley Repertory Theatre"
BASE_URL = "https://www.berkeleyrep.org"
EVENTS_URL = "https://www.berkeleyrep.org/shows"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")

VENUE = "Berkeley Rep, 2025 Addison St, Berkeley, CA 94704"
DEFAULT_HOUR = 19  # 7 PM curtain
DEFAULT_MINUTE = 30
REQUEST_TIMEOUT = 25

# The detail page is server-rendered, so `load` (no settle) gives us the
# ld+json Event list without waiting on any client-side widget.
DETAIL_WAIT_UNTIL = "load"


def matches(url: str) -> bool:
    return "berkeleyrep.org" in url


def _parse_day(text: str) -> date | None:
    """Parse 'Fri, Sep 4, 2026' → date."""
    text = " ".join(text.strip().split())
    try:
        return datetime.strptime(text, "%a, %b %d, %Y").date()
    except ValueError:
        return None


def _parse_event_card(card) -> RawEvent | None:
    title_tag = card.select_one(".title")
    if not title_tag:
        return None
    title = title_tag.get_text(strip=True)

    start_tag = card.select_one(".top-date .start")
    end_tag = card.select_one(".top-date .end")
    if not start_tag:
        return None
    start_day = _parse_day(start_tag.get_text(strip=True))
    end_day = _parse_day(end_tag.get_text(strip=True)) if end_tag else start_day
    if not start_day:
        return None

    a = card.select_one("a.desc") or card.find("a", href=True)
    href = a.get("href") if a else None
    url = urljoin(BASE_URL, href) if href else None

    img_tag = card.select_one("picture img") or card.find("img")
    image_url = img_tag.get("src") if img_tag and img_tag.get("src") else None

    tagline_tag = card.select_one(".tagline")
    tagline = tagline_tag.get_text(" ", strip=True) if tagline_tag else None

    display_range = start_tag.get_text(strip=True)
    if end_day and end_day != start_day and end_tag:
        display_range += " – " + end_tag.get_text(strip=True)
    description = " · ".join(b for b in (display_range, tagline) if b)

    start_time = datetime(
        start_day.year, start_day.month, start_day.day,
        DEFAULT_HOUR, DEFAULT_MINUTE, tzinfo=SOURCE_TZ,
    ).astimezone(timezone.utc)

    return RawEvent(
        title=title,
        start_time=start_time,
        location=VENUE,
        url=url,
        description=description,
        image_url=image_url,
    )


def parse(html: str) -> list[RawEvent]:
    soup = BeautifulSoup(html, "html.parser")
    return [ev for ev in (_parse_event_card(c) for c in soup.select(".eventCard")) if ev is not None]


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iter_event_nodes(html: str):
    """Yield every schema.org `Event` dict from the page's ld+json blocks.

    The detail page carries a `<script type="application/ld+json">` whose value
    is a list mixing per-performance `Event` objects with other schema types
    (BreadcrumbList, WebSite); we keep only the `Event`s.
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
                yield obj


def parse_performances(html: str, *, show: RawEvent) -> list[RawEvent]:
    """Expand one show's detail page into one RawEvent per performance.

    Each performance is a schema.org `Event` with an absolute, tz-explicit
    `startDate` (no year inference needed). Berkeley Rep exposes no per-showing
    ticket URL, so every event links back to the show's detail page. Title,
    location, and image are carried from the run-level `show`.
    """
    events: list[RawEvent] = []
    for node in _iter_event_nodes(html):
        start = _parse_iso(node.get("startDate"))
        if not start:
            continue
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
    """Render one show's detail page and parse its performances.

    Returns [] when the show has no detail URL or the page yields no Event
    JSON-LD — the caller then falls back to the run-level event.
    """
    if not show.url:
        return []
    try:
        html = load_page_html(ctx, show.url, wait_until=DETAIL_WAIT_UNTIL)
    except RateLimited as e:
        print(f"[berkeleyrep] blocked (HTTP {e.status}) at {e.url}, skipping performances", flush=True)
        return []
    return parse_performances(html, show=show)


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    """Fetch Berkeley Rep's what's-on listing, then expand each show to its showings.

    The listing is plain server-rendered HTML (one card per multi-week run). Each
    show's detail page embeds a schema.org `Event` list, so we render it and emit
    one event per performance. If the performances can't be fetched (browser
    error) or a show has none, we fall back to the run-level event.
    """
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[berkeleyrep] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    shows = parse(resp.text)
    return expand_shows(shows, _scrape_show_performances, label="berkeleyrep")
