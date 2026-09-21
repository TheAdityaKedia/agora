"""Berkeley Repertory Theatre events scraper.

Plain server-rendered HTML — no browser needed. `.eventCard` items expose
title, a Fri/Mon-formatted date range, an absolute image URL, and a relative
show detail path. Same show/run model as ATG and A.C.T.: emit one RawEvent
per card at 7:30 PM SF-local on the range's start day, full range preserved
in the description.
"""
from datetime import date, datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "berkeleyrep.org"
NAME = "Berkeley Repertory Theatre"
BASE_URL = "https://www.berkeleyrep.org"
EVENTS_URL = "https://www.berkeleyrep.org/shows"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")

VENUE = "Berkeley Rep, 2025 Addison St, Berkeley, CA 94704"
DEFAULT_HOUR = 19  # 7 PM curtain
DEFAULT_MINUTE = 30
REQUEST_TIMEOUT = 25


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


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[berkeleyrep] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    return parse(resp.text)
