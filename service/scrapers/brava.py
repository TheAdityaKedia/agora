"""Brava Theater Center events scraper.

Squarespace Summary block: `.summary-item-record-type-event` cards with
`.summary-title-link`, `time.summary-metadata-item--date` ("September 17,
2026 – September 19, 2026" or single "September 20, 2026"), and a
`img.summary-thumbnail-image`. One RawEvent per card at the range's start
day at 7:30 PM SF-local, full range preserved in the description.
"""
import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "brava.org"
NAME = "Brava Theater Center"
BASE_URL = "https://www.brava.org"
EVENTS_URL = "https://www.brava.org/events"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "Brava Theater Center, 2781 24th St, San Francisco, CA 94110"
DEFAULT_HOUR = 19
DEFAULT_MINUTE = 30
REQUEST_TIMEOUT = 25

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


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[brava] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    return parse(resp.text)
