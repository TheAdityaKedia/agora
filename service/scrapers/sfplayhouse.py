"""San Francisco Playhouse events scraper.

The homepage has 6 season shows linked as
`/2026-2027-season/<slug>/`, each pointing at a WordPress detail page.
The show titles and posters are on the homepage; the date ranges live on
the detail pages as plain text ("September 26 – November 28, 2026"). We
follow each unique link once — 6 requests plus the homepage — and parse
the range from the detail page text.
"""
import re
import time
from datetime import date, datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "sfplayhouse.org"
NAME = "San Francisco Playhouse"
BASE_URL = "https://sfplayhouse.org"
EVENTS_URL = "https://sfplayhouse.org/"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "San Francisco Playhouse, 450 Post St, San Francisco, CA 94102"
DEFAULT_HOUR = 19
DEFAULT_MINUTE = 30
REQUEST_TIMEOUT = 25
BETWEEN_PAGE_DELAY_S = 0.8

_SHOW_URL_RE = re.compile(r"^https?://(?:www\.)?sfplayhouse\.org/2026-2027-season/([^/]+)/?$")
# "September 26 - November 28, 2026" (any dash variant), or a single date
_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], start=1
)}
_RANGE_RE = re.compile(
    r"(?P<lmon>[A-Z][a-z]+)\s+(?P<lday>\d{1,2})"
    r"(?:\s*[-–—]\s*(?:(?P<rmon>[A-Z][a-z]+)\s+)?(?P<rday>\d{1,2}))?"
    r",\s*(?P<year>\d{4})"
)


def matches(url: str) -> bool:
    return "sfplayhouse.org" in url


def _find_show_urls(soup) -> list[str]:
    seen = set()
    urls = []
    for a in soup.find_all("a", href=True):
        m = _SHOW_URL_RE.match(a["href"])
        if not m or not m.group(1):
            continue
        if a["href"] in seen:
            continue
        seen.add(a["href"])
        urls.append(a["href"])
    return urls


def _parse_range(text: str) -> tuple[date, date] | None:
    text = " ".join(text.split())
    m = _RANGE_RE.search(text)
    if not m:
        return None
    year = int(m.group("year"))
    lmon = _MONTHS.get(m.group("lmon"))
    if lmon is None:
        return None
    try:
        lday = int(m.group("lday"))
    except (TypeError, ValueError):
        return None
    if not m.group("rday"):
        d = date(year, lmon, lday)
        return d, d
    rmon_txt = m.group("rmon") or m.group("lmon")
    rmon = _MONTHS.get(rmon_txt)
    if rmon is None:
        return None
    try:
        rday = int(m.group("rday"))
    except ValueError:
        return None
    try:
        start = date(year, lmon, lday)
        end = date(year, rmon, rday)
    except ValueError:
        return None
    if start > end:
        # Cross-year run given as "Dec 20 – Jan 3, 2028" → start was 2027
        try:
            start = start.replace(year=start.year - 1)
        except ValueError:
            return None
    return start, end


def _parse_detail(html: str, url: str) -> RawEvent | None:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    if not h1:
        return None
    title = h1.get_text(" ", strip=True)
    if not title:
        return None

    range_ = _parse_range(soup.get_text(" ", strip=True))
    if not range_:
        return None
    start_day, end_day = range_

    # OG image is a reliable poster
    og = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
    image_url = og.get("content") if og and og.get("content") else None
    if not image_url:
        img = soup.find("img")
        image_url = img.get("src") if img and img.get("src") else None

    start_time = datetime(
        start_day.year, start_day.month, start_day.day, DEFAULT_HOUR, DEFAULT_MINUTE,
        tzinfo=SOURCE_TZ,
    ).astimezone(timezone.utc)

    description = None
    m = _RANGE_RE.search(soup.get_text(" ", strip=True))
    if m:
        description = m.group(0)

    return RawEvent(
        title=title,
        start_time=start_time,
        location=VENUE,
        url=url,
        description=description,
        image_url=image_url,
    )


def parse(html: str, fetch=None) -> list[RawEvent]:
    """Parse the homepage, then follow each unique season-show URL and parse
    that page. `fetch(url) -> html` lets tests inject stubbed detail pages.
    """
    soup = BeautifulSoup(html, "html.parser")
    urls = _find_show_urls(soup)
    if fetch is None:
        return []
    events: list[RawEvent] = []
    for url in urls:
        try:
            detail_html = fetch(url)
        except Exception as e:
            print(f"[sfplayhouse] detail fetch failed for {url}: {e}", flush=True)
            continue
        ev = _parse_detail(detail_html, url)
        if ev is not None:
            events.append(ev)
    return events


def _requests_fetch(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        raise RuntimeError(f"HTTP {resp.status_code} for {url}")
    resp.raise_for_status()
    time.sleep(BETWEEN_PAGE_DELAY_S)
    return resp.text


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[sfplayhouse] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    return parse(resp.text, fetch=_requests_fetch)
