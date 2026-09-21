"""New Conservatory Theatre Center events scraper.

Uses the Vendini Event Marketing (VEM) plugin — `.vem-single-event` cards on
the /shows/ listing each carry a title, a run-dates block ("Sep 12 - Oct 25,
2026"), a detail URL, and a thumbnail. The date range's earliest span often
omits the year; the latest span always has it, so year comes from the right
side.

The listing shows only a run range, so `parse()` produces one run-level event
per show. Individual showtimes live on each show's detail page, which embeds
one schema.org `Event` JSON-LD block per performance — each with an absolute,
timezone-explicit `startDate` (no year inference needed) and a per-show theatre
name under `location`. `scrape()` fetches each detail page and emits one
RawEvent per performance (see `parse_performances`), mirroring ATG's JSON-LD
approach. If a show has no detail URL or its detail page yields no performances,
we fall back to the run-level event so a show is never dropped.

Note: the detail-page JSON-LD is technically invalid JSON — its `image` field
embeds a raw `<img>` tag with unescaped quotes — so we sanitize that field out
before parsing. There is no per-performance ticket URL, so each showing links
to the show's detail page.
"""
import json
import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA
from scrapers.performances import expand_shows


SOURCE = "nctcsf.org"
NAME = "New Conservatory Theatre Center"
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


# The detail page's JSON-LD `image` field embeds a raw <img> tag with unescaped
# double quotes, which makes the whole block invalid JSON. Strip it before
# parsing. The <img> content never contains a "]", so this stays inside the
# array bounds.
_IMAGE_FIELD_RE = re.compile(r'"image"\s*:\s*\[[^\]]*\]')


def _iter_event_ld(html: str):
    """Yield each schema.org `Event` JSON-LD object on a detail page."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(_IMAGE_FIELD_RE.sub('"image":[]', raw))
        except json.JSONDecodeError:
            continue
        for obj in data if isinstance(data, list) else [data]:
            if isinstance(obj, dict) and obj.get("@type") == "Event":
                yield obj


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _location_str(node: dict) -> str | None:
    loc = node.get("location")
    if not isinstance(loc, dict):
        return None
    name = loc.get("name")
    addr = loc.get("address") if isinstance(loc.get("address"), dict) else {}
    parts = [
        name,
        addr.get("streetAddress"),
        addr.get("addressLocality"),
        addr.get("addressRegion"),
        addr.get("postalCode"),
    ]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def parse_performances(html: str, *, show: RawEvent) -> list[RawEvent]:
    """Expand one show's detail page into one RawEvent per performance.

    The detail page embeds one schema.org `Event` JSON-LD block per showing,
    each with an absolute, timezone-explicit `startDate` (so no year inference
    is needed) and a `location` Place naming the specific theatre. There is no
    per-performance ticket URL, so each showing links back to the show's detail
    page. Returns [] when the page has no `Event` blocks, so the caller falls
    back to the run-level event.
    """
    events: list[RawEvent] = []
    for node in _iter_event_ld(html):
        start = _parse_iso(node.get("startDate"))
        if not start:
            continue
        events.append(RawEvent(
            title=show.title,
            start_time=start.astimezone(timezone.utc),
            location=_location_str(node) or show.location,
            url=node.get("url") or show.url,
            description=show.description,
            image_url=show.image_url,
        ))
    return events


def _scrape_show_performances(_ctx, show: RawEvent) -> list[RawEvent]:
    """Fetch one show's detail page and parse its performances.

    The detail page is plain server-rendered HTML, so we fetch it with
    `requests` (the shared browser `ctx` is unused). Returns [] when the show
    has no detail URL or the page is blocked / has no performances — the caller
    falls back to the run-level event.
    """
    if not show.url:
        return []
    try:
        resp = requests.get(
            show.url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        print(f"[nctcsf] failed to fetch {show.url} ({e}), skipping performances", flush=True)
        return []
    if resp.status_code in (403, 429):
        print(f"[nctcsf] blocked (HTTP {resp.status_code}) at {show.url}, skipping performances", flush=True)
        return []
    if not resp.ok:
        return []
    return parse_performances(resp.text, show=show)


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    """Fetch NCTC's /shows/ listing, then expand each show to its performances.

    The listing is plain server-rendered HTML (one card per show). Each show's
    detail page embeds one schema.org `Event` per performance, so we emit one
    event per showing. Shows with no fetchable performances fall back to the
    run-level event so a show is never dropped.
    """
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[nctcsf] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    shows = parse(resp.text)
    return expand_shows(shows, _scrape_show_performances, label="nctcsf")
