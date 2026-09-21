"""Magic Theatre events scraper.

Squarespace Events collection with `.eventlist-event` cards. Each card
carries an ISO date on `<time class="event-date" datetime="YYYY-MM-DD">` and
a localized start time on `<time class="event-time-localized-start" ...>` /
`<time class="event-time-localized" ...>`. We emit one RawEvent per card at
the first localized start time on the range's start day.
"""
import re
from datetime import date, datetime, time, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "magictheatre.org"
NAME = "Magic Theatre"
BASE_URL = "https://magictheatre.org"
EVENTS_URL = "https://magictheatre.org/calendar"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "Magic Theatre, Fort Mason Center, San Francisco, CA 94123"
DEFAULT_HOUR = 19  # 7 PM if no time listed
REQUEST_TIMEOUT = 25


def matches(url: str) -> bool:
    return "magictheatre.org" in url


def _parse_time(text: str) -> time | None:
    text = " ".join(text.strip().split())
    m = re.match(r"^(\d{1,2}):(\d{2})\s*([APap][Mm])$", text)
    if not m:
        return None
    hour = int(m.group(1)) % 12
    if m.group(3).lower() == "pm":
        hour += 12
    return time(hour, int(m.group(2)))


def _parse_event(card) -> RawEvent | None:
    title_a = card.select_one(".eventlist-title a") or card.select_one(".eventlist-title-link")
    if not title_a:
        return None
    title = title_a.get_text(strip=True)
    href = title_a.get("href")

    date_tag = card.select_one("time.event-date")
    if not date_tag or not date_tag.get("datetime"):
        return None
    try:
        d = datetime.strptime(date_tag["datetime"], "%Y-%m-%d").date()
    except ValueError:
        return None

    time_tag = (card.select_one("time.event-time-localized-start")
                or card.select_one("time.event-time-localized"))
    t = _parse_time(time_tag.get_text(strip=True)) if time_tag else None
    if t is None:
        t = time(DEFAULT_HOUR, 0)

    start_time = datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=SOURCE_TZ).astimezone(timezone.utc)

    img_tag = card.select_one(".eventlist-column-thumbnail img")
    image_url = img_tag.get("data-image") if img_tag and img_tag.get("data-image") else (
        img_tag.get("src") if img_tag else None
    )

    excerpt_tag = card.select_one(".eventlist-excerpt")
    description = excerpt_tag.get_text(" ", strip=True) if excerpt_tag else None

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
    return [ev for ev in (_parse_event(c) for c in soup.select(".eventlist-event")) if ev is not None]


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[magictheatre] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    return parse(resp.text)
