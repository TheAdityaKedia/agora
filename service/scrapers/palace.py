"""Palace of Fine Arts Theatre events scraper.

Small static Webflow site. Each event lives in a `.collection-item.pofa-tpl`
with an <a href="/event/..."> containing an <img> and two text blocks:
  - .text-block-10 → "November 28, 2026 7:00 PM"  (or with weekday prefix)
  - .text-block-9  → title
"""
import re
from datetime import datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "palaceoffinearts.org"
NAME = "Palace of Fine Arts Theatre"
BASE_URL = "https://www.palaceoffinearts.org"
EVENTS_URL = "https://www.palaceoffinearts.org/"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "Palace of Fine Arts Theatre, 3301 Lyon St, San Francisco, CA 94123"
DEFAULT_HOUR = 20
REQUEST_TIMEOUT = 25

# "November 28, 2026 7:00 PM" or "Wednesday, September 23, 2026 8:00PM"
_DATETIME_RE = re.compile(
    r"(?:[A-Za-z]+,\s+)?"                                # optional weekday
    r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4})"
    r"\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>[APap][Mm])"
)

_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], start=1
)}


def matches(url: str) -> bool:
    return "palaceoffinearts.org" in url


def _parse_datetime(text: str):
    m = _DATETIME_RE.search(text or "")
    if not m:
        return None
    month = _MONTHS.get(m.group("month").title())
    if month is None:
        return None
    try:
        hour = int(m.group("hour")) % 12
        if m.group("ampm").lower() == "pm":
            hour += 12
        return datetime(
            int(m.group("year")), month, int(m.group("day")),
            hour, int(m.group("minute")), tzinfo=SOURCE_TZ,
        ).astimezone(timezone.utc)
    except ValueError:
        return None


def _parse_card(card) -> RawEvent | None:
    title_tag = card.select_one(".text-block-9")
    if not title_tag:
        return None
    title = title_tag.get_text(" ", strip=True)
    if not title:
        return None

    date_tag = card.select_one(".text-block-10")
    if not date_tag:
        return None
    date_text = date_tag.get_text(" ", strip=True)
    start_time = _parse_datetime(date_text)
    if not start_time:
        return None

    a = card.find("a", href=True)
    href = a.get("href") if a else None

    img = card.find("img")
    image_url = urljoin(BASE_URL, img["src"]) if img and img.get("src") else None

    return RawEvent(
        title=title,
        start_time=start_time,
        location=VENUE,
        url=urljoin(BASE_URL, href) if href else None,
        description=date_text,
        image_url=image_url,
    )


def parse(html: str) -> list[RawEvent]:
    soup = BeautifulSoup(html, "html.parser")
    return [ev for ev in (_parse_card(c) for c in soup.select(".collection-item.pofa-tpl")) if ev is not None]


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[palace] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    return parse(resp.text)
