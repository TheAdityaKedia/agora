"""Yerba Buena Center for the Arts events scraper.

Server-rendered WordPress. Cards live in `.feature-event-wrap` with:
  - h1 a → title + absolute URL
  - p.date → 'August 7, 2026–January 3, 2027' (full name months, en-dash range)
  - p.type → category ("Exhibitions", "Talks", …)
  - .img inline `background-image: url(...)` → poster
  - Trailing <p> → description blurb

Many YBCA entries are ongoing exhibitions with multi-month date ranges. We
follow the show-model convention: one RawEvent per card at 6 PM SF-local on
the range's start day, full displayed range preserved in the description.
"""
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "ybca.org"
NAME = "Yerba Buena Center for the Arts"
EVENTS_URL = "https://ybca.org/calendar/"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "YBCA, 700 Howard St, San Francisco, CA 94103"
DEFAULT_HOUR = 18  # 6 PM — reasonable for openings/gallery hours
REQUEST_TIMEOUT = 25

_DASH_RE = re.compile(r"[–—-]")
_BG_URL_RE = re.compile(r"url\(\s*(?:['\"])?([^'\")\s]+)")


def matches(url: str) -> bool:
    return "ybca.org" in url


def _parse_full_date(text: str) -> date | None:
    """Parse 'August 7, 2026' → date (full month name)."""
    text = " ".join(text.strip().split())
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_date_range(text: str) -> tuple[date | None, date | None]:
    """Parse 'August 7, 2026–January 3, 2027' or a single 'August 7, 2026'."""
    normalized = _DASH_RE.sub("-", " ".join(text.strip().split()))
    parts = [p.strip() for p in normalized.split("-", 1)]
    left = _parse_full_date(parts[0])
    right = _parse_full_date(parts[1]) if len(parts) == 2 else None
    return left, right


def _extract_image_url(wrap) -> str | None:
    """YBCA emits two card variants: a hero with `background-image:` on .img
    (an anchor), and a list card with a nested <img src=...>. Handle both.
    """
    img_tag = wrap.select_one(".img")
    if img_tag:
        style = img_tag.get("style") or ""
        m = _BG_URL_RE.search(style)
        if m:
            return m.group(1)
    nested = wrap.select_one(".img img") or wrap.find("img")
    if nested and nested.get("src"):
        return nested["src"]
    return None


def _find_title_link(wrap):
    """First anchor inside a heading (h1/h2/h3) that has visible text."""
    for h in wrap.find_all(["h1", "h2", "h3", "h4"]):
        a = h.find("a", href=True)
        if a and a.get_text(strip=True):
            return a
    return None


def _parse_card(wrap) -> RawEvent | None:
    title_a = _find_title_link(wrap)
    if not title_a:
        return None
    title = title_a.get_text(strip=True)
    if not title:
        return None
    href = title_a.get("href")

    date_tag = wrap.select_one("p.date")
    if not date_tag:
        return None
    date_text = date_tag.get_text(" ", strip=True)
    start_day, end_day = _parse_date_range(date_text)
    if not start_day:
        return None

    type_tag = wrap.select_one("p.type")
    category = type_tag.get_text(strip=True) if type_tag else None

    description_bits = [b for b in (category, date_text) if b]
    description = " · ".join(description_bits)

    start_time = datetime(
        start_day.year, start_day.month, start_day.day, DEFAULT_HOUR, 0,
        tzinfo=SOURCE_TZ,
    ).astimezone(timezone.utc)

    return RawEvent(
        title=title,
        start_time=start_time,
        location=VENUE,
        url=href,
        description=description,
        image_url=_extract_image_url(wrap),
    )


def parse(html: str) -> list[RawEvent]:
    soup = BeautifulSoup(html, "html.parser")
    return [ev for ev in (_parse_card(w) for w in soup.select(".feature-event-wrap")) if ev is not None]


def parse_event_description(html: str) -> str | None:
    """Extract the event blurb from a YBCA `/event/` detail page, or None.

    Prefer the `.left-content` body paragraphs (the full blurb, excluding the
    `.section-wrapper` funding credits); fall back to the `og:description`
    one-liner when the body isn't present.
    """
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one(".left-content")
    if content:
        paras = [p.get_text(" ", strip=True) for p in content.find_all("p")]
        text = "\n\n".join(p for p in paras if p)
        if text:
            return text
    og = soup.find("meta", attrs={"property": "og:description"})
    if og and og.get("content"):
        return og["content"].strip() or None
    return None


def _fetch_description(url: str) -> str | None:
    """Fetch a detail page and return its blurb, or None on any error."""
    try:
        resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None
        return parse_event_description(resp.text)
    except requests.RequestException:
        return None


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[ybca] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    events = parse(resp.text)
    # The card only yields category + date; fetch each event's detail page for
    # the real blurb, keeping the category/date string as a fallback.
    for ev in events:
        if ev.url and "/event/" in ev.url:
            desc = _fetch_description(ev.url)
            if desc:
                ev.description = desc
    return events
