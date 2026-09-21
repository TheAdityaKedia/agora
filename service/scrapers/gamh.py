"""Great American Music Hall events scraper.

GAMH publishes its calendar via a SeeTickets widget. Each event lives in
`.seetickets-list-event-container` with:
  - .event-title a[href] → title + Ticketing URL (absolute)
  - .seetickets-list-view-event-image[src] → poster
  - .event-date ("Sun Sep 20") — day + short month, no year
  - .see-showtime ("8:00PM") — the actual start time
  - .event-header, .supporting-talent, .genre — extra description context

Year is inferred from the current SF-local year, rolling forward when the
month sequence goes backward across the list (same approach as Black Bird).
"""
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "gamh.com"
NAME = "Great American Music Hall"
EVENTS_URL = "https://gamh.com/calendar/"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "Great American Music Hall, 859 O'Farrell St, San Francisco, CA 94109"
REQUEST_TIMEOUT = 25

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1
)}
# "Sun Sep 20"
_DATE_RE = re.compile(r"^[A-Za-z]{3}\s+([A-Za-z]{3})\s+(\d{1,2})$")
# "8:00PM" or "8:00 PM"
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*([APap][Mm])$")


def matches(url: str) -> bool:
    return "gamh.com" in url


def _parse_month_day(text: str) -> tuple[int, int] | None:
    text = " ".join(text.strip().split())
    m = _DATE_RE.match(text)
    if not m:
        return None
    month = _MONTHS.get(m.group(1)[:3].title())
    if month is None:
        return None
    try:
        return month, int(m.group(2))
    except ValueError:
        return None


def _parse_time(text: str) -> tuple[int, int] | None:
    text = " ".join(text.strip().split())
    m = _TIME_RE.match(text)
    if not m:
        return None
    hour = int(m.group(1)) % 12
    if m.group(3).lower() == "pm":
        hour += 12
    return hour, int(m.group(2))


def _parse_card(card, year: int) -> tuple[RawEvent | None, int | None]:
    title_a = card.select_one(".event-title a")
    if not title_a:
        return None, None
    title = title_a.get_text(strip=True)
    href = title_a.get("href")

    date_tag = card.select_one(".event-date")
    time_tag = card.select_one(".see-showtime")
    if not (date_tag and time_tag):
        return None, None
    md = _parse_month_day(date_tag.get_text(strip=True))
    hm = _parse_time(time_tag.get_text(strip=True))
    if not (md and hm):
        return None, None
    month, day = md
    hour, minute = hm

    try:
        start_time = datetime(year, month, day, hour, minute, tzinfo=SOURCE_TZ).astimezone(timezone.utc)
    except ValueError:
        return None, month

    img_tag = card.select_one(".seetickets-list-view-event-image")
    image_url = img_tag.get("src") if img_tag and img_tag.get("src") else None

    bits = []
    for sel in (".event-header", ".supporting-talent", ".genre"):
        el = card.select_one(sel)
        if el:
            txt = el.get_text(" ", strip=True)
            if txt:
                bits.append(txt)
    description = " · ".join(bits) if bits else None

    return RawEvent(
        title=title,
        start_time=start_time,
        location=VENUE,
        url=href if href else None,
        description=description,
        image_url=image_url,
    ), month


def parse(html: str) -> list[RawEvent]:
    soup = BeautifulSoup(html, "html.parser")
    year = datetime.now(SOURCE_TZ).year
    prev_month: int | None = None
    events: list[RawEvent] = []
    for card in soup.select(".seetickets-list-event-container"):
        # Peek month for year rollover
        date_tag = card.select_one(".event-date")
        peek = _parse_month_day(date_tag.get_text(strip=True)) if date_tag else None
        if peek and prev_month is not None and peek[0] < prev_month:
            year += 1
        ev, month = _parse_card(card, year)
        if ev is not None:
            events.append(ev)
        if month is not None:
            prev_month = month
    return events


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[gamh] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    return parse(resp.text)
