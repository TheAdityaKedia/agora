"""New Conservatory Theatre Center events scraper.

Uses the Vendini Event Marketing (VEM) plugin — `.vem-single-event` cards
each carry a title, a run-dates block ("Sep 12 - Oct 25, 2026"), a detail
URL, and a thumbnail. The date range's earliest span often omits the year;
the latest span always has it, so year comes from the right side. Same
one-event-per-show approach as ATG / A.C.T. — start on the range's first
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


SOURCE = "nctcsf.org"
BASE_URL = "https://nctcsf.org"
EVENTS_URL = "https://nctcsf.org/shows/"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "New Conservatory Theatre Center, 25 Van Ness Ave, San Francisco, CA 94102"
DEFAULT_HOUR = 19
DEFAULT_MINUTE = 30
REQUEST_TIMEOUT = 25

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1
)}


def matches(url: str) -> bool:
    return "nctcsf.org" in url


def _parse_month_day_year(text: str) -> date | None:
    """Parse 'Oct 25, 2026'."""
    text = " ".join(text.strip().split())
    m = re.match(r"^([A-Za-z]{3,})\s+(\d{1,2}),\s*(\d{4})$", text)
    if not m:
        return None
    month = _MONTHS.get(m.group(1)[:3].title())
    if month is None:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(2)))
    except ValueError:
        return None


def _parse_month_day(text: str, fallback_year: int) -> date | None:
    """Parse 'Sep 12' using fallback_year (from the right-side span)."""
    text = " ".join(text.strip().split())
    m = re.match(r"^([A-Za-z]{3,})\s+(\d{1,2})$", text)
    if not m:
        return None
    month = _MONTHS.get(m.group(1)[:3].title())
    if month is None:
        return None
    try:
        return date(fallback_year, month, int(m.group(2)))
    except ValueError:
        return None


def _parse_range(card) -> tuple[date, date] | None:
    """Return (start, end) from the .vem-single-event-run-dates block."""
    earliest = card.select_one(".vem-earliest")
    latest = card.select_one(".vem-latest")
    if latest:
        end_date = _parse_month_day_year(latest.get_text(strip=True))
    else:
        end_date = None
    if not end_date:
        return None
    if earliest:
        start_date = (_parse_month_day_year(earliest.get_text(strip=True))
                      or _parse_month_day(earliest.get_text(strip=True), end_date.year))
        if start_date and start_date > end_date:
            start_date = start_date.replace(year=start_date.year - 1)
    else:
        start_date = end_date
    return start_date or end_date, end_date


def _parse_event(card) -> RawEvent | None:
    title_tag = card.select_one(".vem-single-event-title")
    if not title_tag:
        return None
    title = title_tag.get_text(strip=True)

    range_ = _parse_range(card)
    if not range_:
        return None
    start_day, end_day = range_

    a = card.select_one(".vem-single-event-thumbnail a") or card.find("a", href=True)
    href = a.get("href") if a else None

    img = card.find("img")
    image_url = img.get("src") if img and img.get("src") else None

    fields = [f.get_text(" ", strip=True) for f in card.select(".field-set-value")]
    range_text = card.select_one(".vem-single-event-run-dates")
    range_display = range_text.get_text(" ", strip=True) if range_text else None
    description = " · ".join(b for b in ([range_display] + fields[:3]) if b) or None

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
    return [ev for ev in (_parse_event(c) for c in soup.select(".vem-single-event")) if ev is not None]


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[nctcsf] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    return parse(resp.text)
