"""Presidio Theatre events scraper.

Static HTML: `.show-card` containers. Each has:
  - <a href="/show-details/..."> (absolute)
  - .mainimg-flip img[data-src|src] — poster
  - .show-cat — event category ("DANCE", "MUSIC", …)
  - <h3>Title <span>Sep 20, 2026</span></h3> — title + date in one heading

The date is embedded as a <span> inside the h3. We split them and parse the
span with "%b %d, %Y".
"""
import re
from datetime import datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "presidiotheatre.org"
BASE_URL = "https://www.presidiotheatre.org"
EVENTS_URL = "https://www.presidiotheatre.org/shows"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "Presidio Theatre, 99 Moraga Ave, San Francisco, CA 94129"
DEFAULT_HOUR = 19
DEFAULT_MINUTE = 30
REQUEST_TIMEOUT = 25


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
    """The h3 contains a title text node plus a <span> with the date."""
    date_span = h3.find("span")
    date_text = date_span.get_text(strip=True) if date_span else ""
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

    day = _parse_date(date_text)
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
    soup = BeautifulSoup(html, "html.parser")
    return [ev for ev in (_parse_card(c) for c in soup.select(".show-card")) if ev is not None]


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[presidio] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    return parse(resp.text)
