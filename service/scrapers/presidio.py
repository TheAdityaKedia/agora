"""Presidio Theatre events scraper.

The `/shows` listing is plain server-rendered HTML — one `.show-card` per show
(each title appears once). Each card carries:
  - <a href="/show-details/..."> (absolute)
  - .mainimg-flip img[data-src|src] — poster
  - .show-cat — event category ("DANCE", "MUSIC", …)
  - <h3>Title <span>Sep 20, 2026</span></h3> — title + date/range in one heading
    The span holds either a single date ("Sep 26, 2026") or a run range
    ("May 20, 2027 - May 22, 2027").

A card is a *show*, not a single performance: a multi-day run collapses into one
card, and its individual showtimes live on the show's `/show-details/...` page.
That detail page is also static HTML and embeds a schema.org `TheaterEvent`
whose `offers[]` lists every performance with an absolute, tz-explicit datetime
(`availabilityStarts`/`validFrom`) and a unique per-performance ticket URL
(`EventInstanceId=...`). A single-night show has exactly one offer.

So `scrape()` emits one RawEvent per performance (see `parse_performances`). When
a show's detail page has no parseable performances (missing/blocked page, no
TheaterEvent, or no offers), we fall back to a single run-level event at 7:30 PM
SF-local on the run's start day, so a show is never dropped.
"""
import json
import re
from datetime import datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA
from scrapers.performances import expand_shows


SOURCE = "presidiotheatre.org"
NAME = "Presidio Theatre"
BASE_URL = "https://www.presidiotheatre.org"
EVENTS_URL = "https://www.presidiotheatre.org/shows"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "Presidio Theatre, 99 Moraga Ave, San Francisco, CA 94129"
DEFAULT_HOUR = 19
DEFAULT_MINUTE = 30
REQUEST_TIMEOUT = 25

# A single "Mon DD, YYYY" date anywhere in a heading span (the run-start for a
# range like "May 20, 2027 - May 22, 2027").
_DATE_RE = re.compile(r"[A-Z][a-z]{2,8}\s+\d{1,2},\s*\d{4}")


def matches(url: str) -> bool:
    return "presidiotheatre.org" in url


def _parse_date(text: str):
    text = " ".join(text.strip().split())
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _split_title_and_date(h3):
    """The h3 contains a title text node plus a <span> with the date/range."""
    date_span = h3.find("span")
    date_text = date_span.get_text(" ", strip=True) if date_span else ""
    date_text = " ".join(date_text.split())  # collapse whitespace/newlines
    # Title is everything except the span
    if date_span:
        date_span.extract()
    title = h3.get_text(" ", strip=True)
    return title, date_text


def _parse_card(card) -> RawEvent | None:
    h3 = card.find("h3")
    if not h3:
        return None
    # Clone so we don't mutate the original when extracting the span
    from copy import copy
    h3_copy = copy(h3)
    title, date_text = _split_title_and_date(h3_copy)
    if not title:
        return None

    # date_text may be a single date or a range; use the first date as the
    # run-start so range cards aren't dropped.
    m = _DATE_RE.search(date_text)
    day = _parse_date(m.group(0)) if m else None
    if not day:
        return None

    a = card.find("a", href=True)
    href = a.get("href") if a else None

    img = card.select_one(".mainimg-flip img") or card.find("img")
    image_url = None
    if img:
        image_url = img.get("data-src") or img.get("src")

    cat_tag = card.select_one(".show-cat")
    category = cat_tag.get_text(" ", strip=True) if cat_tag else None
    description = " · ".join(b for b in (category, date_text) if b) or None

    start_time = datetime(
        day.year, day.month, day.day, DEFAULT_HOUR, DEFAULT_MINUTE,
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
    """Parse the /shows listing into run-level RawEvents (one per card)."""
    soup = BeautifulSoup(html, "html.parser")
    return [ev for ev in (_parse_card(c) for c in soup.select(".show-card")) if ev is not None]


def _find_theater_event(html: str) -> dict | None:
    """Return the show's schema.org TheaterEvent JSON-LD object, or None."""
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
            if isinstance(obj, dict) and obj.get("@type") == "TheaterEvent":
                return obj
    return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_performances(html: str, *, show: RawEvent) -> list[RawEvent]:
    """Expand one show's detail page into one RawEvent per performance.

    Presidio embeds a schema.org TheaterEvent whose `offers[]` lists every
    performance with a tz-explicit datetime (`availabilityStarts`, falling back
    to `validFrom`) and a per-performance ticket `url`. A single-night show has
    exactly one offer. Returns [] when there's no TheaterEvent or no offers, so
    the caller falls back to the run-level event.
    """
    data = _find_theater_event(html)
    if not data:
        return []
    offers = data.get("offers")
    if not isinstance(offers, list):
        return []
    events: list[RawEvent] = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        start = _parse_iso(offer.get("availabilityStarts") or offer.get("validFrom"))
        if not start:
            continue
        url = offer.get("url") or show.url
        events.append(RawEvent(
            title=show.title,
            start_time=start.astimezone(timezone.utc),
            location=show.location,
            url=url,
            description=show.description,
            image_url=show.image_url,
        ))
    return events


def _scrape_show_performances(ctx, show: RawEvent) -> list[RawEvent]:
    """Fetch one show's detail page (static HTML) and parse its performances.

    Returns [] when the show has no detail URL or its page yields no parseable
    performances — the caller falls back to the run-level event. `ctx` (the
    shared browser context) is unused: the detail page is plain HTML.
    """
    if not show.url:
        return []
    try:
        resp = requests.get(show.url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
        if resp.status_code in (403, 429):
            print(f"[presidio] blocked (HTTP {resp.status_code}) at {show.url}, skipping performances", flush=True)
            return []
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[presidio] fetch failed for {show.url}: {e}", flush=True)
        return []
    return parse_performances(resp.text, show=show)


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    """Fetch Presidio's /shows listing, then expand each show to its performances.

    The listing and detail pages are both static HTML. We emit one event per
    performance (parsed from the detail page's TheaterEvent `offers[]`), falling
    back to the run-level event when a show has no parseable performances.
    """
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[presidio] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    shows = parse(resp.text)
    return expand_shows(shows, _scrape_show_performances, label="presidio")
