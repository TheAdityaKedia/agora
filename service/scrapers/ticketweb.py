"""Shared helpers for venue sites built on TicketWeb's WordPress plugin.

Bimbo's 365 Club, August Hall and Feinstein's at the Nikko run the same
TicketWeb/Ticketmaster WordPress plugin: listing pages of ``.tw-section``
cards (``/events/``, paginated by each page's own next link) linking to one
``/tm-event/<slug>/`` page per show date. The listing cards write dates
differently on each site ("October" + "9", "10.09", "October 07, 2026") and
August Hall's carry no time, so the event pages are the source of truth:
``.tw-event-date-complete`` ("Wednesday, October 7, 2026" or "Wed | Oct 14"
without a year), ``.tw-event-time`` ("Show: 8:00 pm"), ``.tw-description``,
``.tw-attractions`` (openers) and the poster. A date without a year is placed
by its weekday (scrapers/datetext.py). One fetch per show page, a few in
parallel.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers import datetext
from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 25
MAX_PAGES = 15
PAGE_WORKERS = 5

_WEEKDAYS = {w[:3]: w for w in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}
_MONTHS = {m[:3]: m for m in ("january", "february", "march", "april", "may", "june", "july",
                               "august", "september", "october", "november", "december")}
_FULL_DATE_RE = re.compile(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})")
_PARTS_RE = re.compile(r"\b([A-Za-z]{3})[a-z]*\b\W+([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})\b")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])\.?m", re.I)
# Description sentences that are box-office logistics, not about the show.
_LOGISTICS_RE = re.compile(
    r"^(all ages|21\+|18\+|ages \d+\+|please note|doors|no refunds|tickets? (are|will)|"
    r"delivery delay|this event is)", re.I)


# Box-office phrases removed wherever they appear in the text; the VIP and
# "please note" ones run to the end of the description.
_STRIP_RES = [
    re.compile(r"\bVIP Includes:.*$", re.I | re.S),
    re.compile(r"\bPlease note\b.*$", re.I | re.S),
    re.compile(r"\b(two|\d)[- ]drink minimum( required| req\.?)?\.?", re.I),
    re.compile(r"\b(21|18)\+ only\.?", re.I),
]


def _time(text: str | None) -> tuple[int, int] | None:
    m = _TIME_RE.search(text or "")
    if not m:
        return None
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    return hour, int(m.group(2))


def parse_start(date_text: str | None, time_text: str | None, today: date) -> datetime | None:
    """Start in UTC from an event page's date and time strings. Pure."""
    hm = _time(time_text) or (20, 0)  # no time listed: an 8pm show
    m = _FULL_DATE_RE.search(date_text or "")
    if m and m.group(1)[:3].lower() in _MONTHS:
        month = list(_MONTHS).index(m.group(1)[:3].lower()) + 1
        try:
            local = datetime(int(m.group(3)), month, int(m.group(2)), *hm, tzinfo=SOURCE_TZ)
        except ValueError:
            return None
        return local.astimezone(timezone.utc)
    m = _PARTS_RE.search(date_text or "")
    if not m or m.group(1).lower() not in _WEEKDAYS or m.group(2).lower() not in _MONTHS:
        return None
    hour, minute = hm
    ampm = "pm" if hour >= 12 else "am"
    text = (f"{_WEEKDAYS[m.group(1).lower()]}, {_MONTHS[m.group(2).lower()]} {int(m.group(3))} "
            f"at {hour % 12 or 12}:{minute:02d} {ampm}")
    return datetext.parse_weekday_date(text, today)


def _text(soup, selector: str) -> str:
    el = soup.select_one(selector)
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def _description(soup) -> str | None:
    desc = _text(soup, ".tw-description")
    for rx in _STRIP_RES:
        desc = rx.sub(" ", desc)
    desc = re.sub(r"\s+", " ", desc).strip()
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", desc) if s and not _LOGISTICS_RE.search(s)]
    parts = [_text(soup, ".tw-attractions"), " ".join(sentences), _text(soup, ".tw-genre")]
    text = " · ".join(p for p in parts if p)
    return text or None


def parse_event(html: str, *, url: str, venue: str, today: date) -> RawEvent | None:
    """One ``/tm-event/`` page → RawEvent. Pure."""
    soup = BeautifulSoup(html, "html.parser")
    title = _text(soup, ".tw-name")
    start = parse_start(_text(soup, ".tw-event-date-complete") or _text(soup, ".tw-event-date"),
                        _text(soup, ".tw-event-time") or _text(soup, ".tw-event-door-time"), today)
    if not (title and start):
        return None
    img = soup.select_one(".tw-event-image img, img.event-img")
    return RawEvent(
        title=title,
        start_time=start,
        location=venue,
        url=url,
        description=_description(soup),
        image_url=(img.get("src") or img.get("data-src")) if img else None,
    )


def listing_links(html: str) -> list[str]:
    """Show-page links on a listing page, in order. Pure."""
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for a in soup.select(".tw-name a[href]"):
        href = a["href"]
        if "/tm-event/" in href and href not in links:
            links.append(href)
    return links


def next_page(html: str, current: int) -> str | None:
    """The listing's link to page ``current + 1`` (paths differ per site:
    Bimbo's paginates ``/shows/page/N/``, the others ``/events/page/N/``). Pure."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a[href]"):
        m = re.search(r"/page/(\d+)/?$", a["href"])
        if m and int(m.group(1)) == current + 1:
            return a["href"]
    return None


def scrape_site(site_base: str, *, venue: str, tag: str = "ticketweb") -> list[RawEvent]:
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    base = site_base.rstrip("/")
    links: list[str] = []
    page: str | None = f"{base}/events/"
    for n in range(1, MAX_PAGES + 1):
        if not page:
            break
        try:
            r = session.get(page, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            print(f"[{tag}] listing fetch failed ({page}): {e}", flush=True)
            break
        if r.status_code != 200:
            break
        new = [u for u in listing_links(r.text) if u not in links]
        if not new:
            break
        links.extend(new)
        page = next_page(r.text, n)

    today = datetime.now(SOURCE_TZ).date()

    def fetch(u: str) -> RawEvent | None:
        try:
            r = session.get(u, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException:
            return None
        return parse_event(r.text, url=u, venue=venue, today=today)

    with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pool:
        events = [e for e in pool.map(fetch, links) if e]
    print(f"[{tag}] {len(events)} shows from {len(links)} pages", flush=True)
    return sorted(events, key=lambda e: e.start_time)
