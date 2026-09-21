"""Great Star Theater events scraper.

Static HTML: `.event-list-item` (an <a>) with:
  - h3 (title)
  - .event-time ("September 25 - 26, 2026" or "September 25, 2026")
  - .event-image img (may be site-relative)
  - .event-desc for the blurb

Many events on the Great Star page are third-party — links go to
TicketTailor. That's fine; we hotlink whatever href they provide.
"""
import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "greatstartheater.org"
BASE_URL = "https://www.greatstartheater.org"
EVENTS_URL = "https://www.greatstartheater.org/whats-playing"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "Great Star Theater, 636 Jackson St, San Francisco, CA 94133"
DEFAULT_HOUR = 20  # 8 PM — comedy/show default
REQUEST_TIMEOUT = 25

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
    """
    text = _DASH_RE.sub("-", " ".join(text.strip().split()))
    # Single date first
    d = _parse_full_date(text)
    if d:
        return d
    # Range
    parts = [p.strip() for p in text.split("-", 1)]
    if len(parts) != 2:
        return None
    right = _parse_full_date(parts[1])
    if not right:
        return None
    # Left may be 'September 25' (no year) → prepend right's year via day-only
    left = _parse_full_date(f"{parts[0]}, {right.year}")
    if left:
        return left
    # Or left could be day-only "25" — same month as right
    try:
        return date(right.year, right.month, int(parts[0]))
    except ValueError:
        return right


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


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[greatstar] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    return parse(resp.text)
