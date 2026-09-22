"""The Warfield events scraper.

Static HTML: `.entry.warfield` cards inside the site's `.event_list` widget.
Each card carries:
  - .title (event title)
  - .date  ("Mon, Sep 21, 2026")
  - .time  ("Show 8:00 PM")
  - .thumb img (poster)
  - A detail-page link and a "Buy Tickets" AXS link.
"""
import re
from datetime import datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "thewarfieldtheatre.com"
NAME = "The Warfield"
BASE_URL = "https://www.thewarfieldtheatre.com"
EVENTS_URL = "https://www.thewarfieldtheatre.com/events"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "The Warfield, 982 Market St, San Francisco, CA 94102"
DEFAULT_HOUR = 20
REQUEST_TIMEOUT = 25

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([APap][Mm])")


def matches(url: str) -> bool:
    return "thewarfieldtheatre.com" in url


def _parse_date(text: str):
    text = " ".join(text.strip().split())
    for fmt in ("%a, %b %d, %Y", "%A, %b %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_time(text: str):
    m = _TIME_RE.search(text or "")
    if not m:
        return None
    hour = int(m.group(1)) % 12
    if m.group(3).lower() == "pm":
        hour += 12
    return hour, int(m.group(2))


def _parse_entry(entry) -> RawEvent | None:
    title_tag = entry.select_one(".title") or entry.select_one(".carousel_item_title_small")
    if not title_tag:
        return None
    title = title_tag.get_text(" ", strip=True)

    date_tag = entry.select_one(".date")
    if not date_tag:
        return None
    day = _parse_date(date_tag.get_text(" ", strip=True))
    if not day:
        return None

    time_tag = entry.select_one(".time")
    hm = _parse_time(time_tag.get_text(" ", strip=True)) if time_tag else None
    hour, minute = hm if hm else (DEFAULT_HOUR, 0)

    # Detail page URL (Warfield-hosted); fall back to first anchor
    detail_a = None
    for a in entry.find_all("a", href=True):
        if "/events/detail/" in a["href"]:
            detail_a = a
            break
    a = detail_a or entry.find("a", href=True)
    href = a.get("href") if a else None

    img = entry.select_one(".thumb img") or entry.find("img")
    image_url = None
    if img:
        src = img.get("src") or img.get("data-src")
        if src:
            image_url = urljoin(BASE_URL, src)

    start_time = datetime(day.year, day.month, day.day, hour, minute, tzinfo=SOURCE_TZ).astimezone(timezone.utc)

    return RawEvent(
        title=title,
        start_time=start_time,
        location=VENUE,
        url=urljoin(BASE_URL, href) if href else None,
        description=None,
        image_url=image_url,
    )


def parse(html: str) -> list[RawEvent]:
    soup = BeautifulSoup(html, "html.parser")
    return [ev for ev in (_parse_entry(e) for e in soup.select(".entry.warfield")) if ev is not None]


def parse_detail_description(html: str) -> str | None:
    """Extract the event blurb from a Warfield detail page, or None.

    The listing has no description; the blurb lives on the detail page in a
    `div.bio` ("Artist Information") block. Its `<p>` paragraphs are the real
    copy — the `<h3>` label and the sibling `.description` (ADA/ticketing
    policy) block are excluded by only reading `.bio p`.
    """
    soup = BeautifulSoup(html, "html.parser")
    bio = soup.select_one(".bio")
    if not bio:
        return None
    paras = [p.get_text(" ", strip=True) for p in bio.find_all("p")]
    text = "\n\n".join(p for p in paras if p)
    return text or None


def _fetch_description(url: str) -> str | None:
    """Fetch a Warfield detail page and return its blurb, or None on any error."""
    try:
        resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None
        return parse_detail_description(resp.text)
    except requests.RequestException:
        return None


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[warfield] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    events = parse(resp.text)
    # The listing carries no blurb; fetch each event's detail page (the same URL
    # we link to) for its description.
    for ev in events:
        if ev.url and "/events/detail/" in ev.url:
            ev.description = _fetch_description(ev.url)
    return events
