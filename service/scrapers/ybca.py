"""Yerba Buena Center for the Arts events scraper.

Server-rendered WordPress. Cards live in `.feature-event-wrap` with:
  - h1 a → title + absolute URL
  - p.date → 'August 7, 2026–January 3, 2027' (full name months, en-dash range)
  - p.type → category ("Exhibitions", "Talks", …)
  - .img inline `background-image: url(...)` → poster
  - Trailing <p> → description blurb

Many YBCA entries are ongoing exhibitions with multi-month date ranges. We
follow the show-model convention: one RawEvent per card at 6 PM SF-local on
the range's start day, full displayed range preserved in the description.
"""
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "ybca.org"
EVENTS_URL = "https://ybca.org/calendar/"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "YBCA, 700 Howard St, San Francisco, CA 94103"
DEFAULT_HOUR = 18  # 6 PM — reasonable for openings/gallery hours
REQUEST_TIMEOUT = 25

_DASH_RE = re.compile(r"[–—-]")
_BG_URL_RE = re.compile(r"url\(\s*(?:['\"])?([^'\")\s]+)")


def matches(url: str) -> bool:
    return "ybca.org" in url


def _parse_full_date(text: str) -> date | None:
    """Parse 'August 7, 2026' → date (full month name)."""
    text = " ".join(text.strip().split())
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_date_range(text: str) -> tuple[date | None, date | None]:
    """Parse 'August 7, 2026–January 3, 2027' or a single 'August 7, 2026'."""
    normalized = _DASH_RE.sub("-", " ".join(text.strip().split()))
    parts = [p.strip() for p in normalized.split("-", 1)]
    left = _parse_full_date(parts[0])
    right = _parse_full_date(parts[1]) if len(parts) == 2 else None
    return left, right


def _extract_image_url(wrap) -> str | None:
    """YBCA emits two card variants: a hero with `background-image:` on .img
    (an anchor), and a list card with a nested <img src=...>. Handle both.
    """
    img_tag = wrap.select_one(".img")
    if img_tag:
        style = img_tag.get("style") or ""
        m = _BG_URL_RE.search(style)
        if m:
            return m.group(1)
    nested = wrap.select_one(".img img") or wrap.find("img")
    if nested and nested.get("src"):
        return nested["src"]
    return None


def _find_title_link(wrap):
    """First anchor inside a heading (h1/h2/h3) that has visible text."""
    for h in wrap.find_all(["h1", "h2", "h3", "h4"]):
        a = h.find("a", href=True)
        if a and a.get_text(strip=True):
            return a
    return None


def _parse_card(wrap) -> RawEvent | None:
    title_a = _find_title_link(wrap)
    if not title_a:
        return None
    title = title_a.get_text(strip=True)
    if not title:
        return None
    href = title_a.get("href")

    date_tag = wrap.select_one("p.date")
    if not date_tag:
        return None
    date_text = date_tag.get_text(" ", strip=True)
    start_day, end_day = _parse_date_range(date_text)
    if not start_day:
        return None

    type_tag = wrap.select_one("p.type")
    category = type_tag.get_text(strip=True) if type_tag else None

    description_bits = [b for b in (category, date_text) if b]
    description = " · ".join(description_bits)

    start_time = datetime(
        start_day.year, start_day.month, start_day.day, DEFAULT_HOUR, 0,
        tzinfo=SOURCE_TZ,
    ).astimezone(timezone.utc)

    return RawEvent(
        title=title,
        start_time=start_time,
        location=VENUE,
        url=href,
        description=description,
        image_url=_extract_image_url(wrap),
    )


def parse(html: str) -> list[RawEvent]:
    soup = BeautifulSoup(html, "html.parser")
    return [ev for ev in (_parse_card(w) for w in soup.select(".feature-event-wrap")) if ev is not None]


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[ybca] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    return parse(resp.text)
