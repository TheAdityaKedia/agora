"""San Francisco Playhouse events scraper.

The homepage has 6 season shows linked as
`/2026-2027-season/<slug>/`, each pointing at a WordPress detail page.
The show titles and posters are on the homepage; the date ranges live on
the detail pages as plain text ("September 26 – November 28, 2026"). We
follow each unique link once — 6 requests plus the homepage — and parse
the range from the detail page text to build one run-level show per link.

The detail page also carries the real synopsis in its server-rendered body
(first `<p>` of `div.entry div.three_fifth.last`), which `_parse_detail` reads
for each show's description, falling back to the date range when absent.

Users browse the calendar by day, so we then expand each run into one event
per individual performance. The detail page loads a VBO Tickets script
(`connect.vbotickets.com/googleeventschema/<eid>`) that injects a
`<script type="application/ld+json">` whose `@graph` lists one schema.org
Event per showing — each with a local (no-offset) `startDate`. That script only
runs in a real browser, so `scrape()` renders each detail page in headless
Chromium and `parse_performances()` reads the injected JSON-LD. Each performance
keeps the show's sfplayhouse.org `url` (not the per-seat VBO `offers.url` deep
link). When a show exposes no performance graph (browser error, or a page
without the widget), we fall back to the single run-level event so a show is
never dropped.
"""
import json
import re
import time
from datetime import date, datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA, RateLimited, load_page_html
from scrapers.performances import expand_shows


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
# The VBO calendar script injects its per-performance JSON-LD after load; give
# it a moment to fetch and paint.
PERF_WAIT_UNTIL = "load"
PERF_SETTLE_MS = 6000

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


def _extract_synopsis(soup) -> str | None:
    """Return the show's synopsis from the sfplayhouse.org detail page.

    The WordPress body renders the synopsis as the first `<p>` inside
    `div.entry div.three_fifth.last`; the later `<p>` tags in that block are
    press pull-quotes/reviews, so we take only the first non-empty paragraph.
    Returns None when the block is absent (caller falls back to the date range).
    """
    block = soup.select_one("div.entry div.three_fifth.last") or soup.select_one("div.three_fifth.last")
    if block is None:
        return None
    for p in block.find_all("p"):
        text = p.get_text(" ", strip=True)
        if text:
            return text
    return None


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

    # Prefer the real synopsis; fall back to the run's date range when absent.
    description = _extract_synopsis(soup)
    if not description:
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


def _load_ld_json(raw: str) -> object | None:
    """Parse an ld+json script body, unwrapping a VBO JS wrapper if present.

    In a rendered page the injected `<script>` body is plain JSON, but a raw
    fetch of the VBO endpoint wraps it as `script.text = '{...}';` — handle both.
    """
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"script\.text\s*=\s*'(.*)'\s*;", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
    return None


def _find_event_graph(html: str) -> list[dict]:
    """Return the list of schema.org Event nodes injected by the VBO widget."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        data = _load_ld_json(tag.string or tag.get_text())
        if not isinstance(data, dict):
            continue
        graph = data.get("@graph")
        if not isinstance(graph, list):
            continue
        events = [n for n in graph if isinstance(n, dict) and n.get("@type") == "Event"]
        if events:
            return events
    return []


def _parse_local_iso(value: str | None) -> datetime | None:
    """Parse a VBO local ISO stamp ('2026-09-25T20:00', no tz) as SF-local UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SOURCE_TZ)
    return dt.astimezone(timezone.utc)


def parse_performances(html: str, *, show: RawEvent) -> list[RawEvent]:
    """Expand one show's rendered detail page into one RawEvent per performance.

    Reads the VBO-injected schema.org `@graph` of Event nodes; each carries a
    local `startDate` (interpreted as SF-local). Every performance links to the
    show's sfplayhouse.org page (`show.url`), not the per-seat VBO ticketing
    deep link (`offers.url`, e.g. `.../eventdate/<slug>/695538`), which isn't a
    useful landing page — distinct `start_time`s keep the showings apart.
    Returns [] when no Event graph is present so the caller falls back to the
    run-level event.
    """
    events: list[RawEvent] = []
    for node in _find_event_graph(html):
        start = _parse_local_iso(node.get("startDate"))
        if not start:
            continue
        events.append(RawEvent(
            title=show.title,
            start_time=start,
            location=show.location,
            url=show.url,
            description=show.description,
            image_url=show.image_url,
        ))
    return events


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


def _scrape_show_performances(ctx, show: RawEvent) -> list[RawEvent]:
    """Render one show's detail page and parse its per-performance showings.

    Returns [] when the show has no detail URL or the page exposes no VBO Event
    graph — the caller falls back to the run-level event in that case.
    """
    if not show.url:
        return []
    try:
        html = load_page_html(ctx, show.url, wait_until=PERF_WAIT_UNTIL, settle_ms=PERF_SETTLE_MS)
    except RateLimited as e:
        print(f"[sfplayhouse] blocked (HTTP {e.status}) at {e.url}, skipping performances", flush=True)
        return []
    return parse_performances(html, show=show)


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    """Fetch the homepage, build one run-level show per season link, then expand
    each into one event per performance via its rendered VBO calendar."""
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[sfplayhouse] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    shows = parse(resp.text, fetch=_requests_fetch)
    return expand_shows(shows, _scrape_show_performances, label="sfplayhouse")
