"""Shared helpers for Another Planet Entertainment venue sites.

The Castro Theatre, the Fox Theater (Oakland) and the Greek Theatre
(Berkeley) run one WordPress template whose show cards carry schema.org
microdata: an ``h2.show-title`` (``itemprop="name"``) inside a link to the
show page, and a ``.date-show`` with ``itemprop="startDate"`` whose
``content`` is the local wall time ("October 6, 2026 8:00pm"). An optional
``.topline`` holds the tour name and ``.support`` the openers. The full
list's cards (the Castro's) have no microdata, only a ``.time-show`` line
("Friday, October 09, 2026 Doors: 7:00 pm | Show: 8:00 pm"); both are read,
show time preferred over doors. The homepage lists every announced show
(plus a featured carousel that repeats some), so cards are de-duplicated by
(show page, start).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 25
DEFAULT_HOUR = 20  # a dated card with no time: assume an 8pm show

_DATE_RE = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})")
_SHOW_TIME_RE = re.compile(r"Show:\s*(\d{1,2}:\d{2}\s*[ap]m)", re.I)
_ANY_TIME_RE = re.compile(r"(\d{1,2}:\d{2}\s*[ap]m)", re.I)


def _local(date_text: str, time_text: str | None) -> datetime | None:
    try:
        day = datetime.strptime(date_text, "%B %d, %Y")
    except ValueError:
        return None
    if time_text:
        try:
            t = datetime.strptime(re.sub(r"\s+", "", time_text).lower(), "%I:%M%p")
            day = day.replace(hour=t.hour, minute=t.minute)
        except ValueError:
            day = day.replace(hour=DEFAULT_HOUR)
    else:
        day = day.replace(hour=DEFAULT_HOUR)
    return day.replace(tzinfo=SOURCE_TZ).astimezone(timezone.utc)


def _card_start(card) -> datetime | None:
    """Start from the microdata ``content`` ("October 6, 2026 8:00pm"), else
    from the ``.time-show`` text ("Friday, October 09, 2026 Doors: 7:00 pm |
    Show: 8:00 pm")."""
    for text in (
        (card.select_one("[itemprop=startDate]") or {}).get("content") if card.select_one("[itemprop=startDate]") else None,
        card.select_one(".time-show").get_text(" ", strip=True) if card.select_one(".time-show") else None,
    ):
        if not text:
            continue
        date = _DATE_RE.search(text)
        if not date:
            continue
        rest = text[date.end():]
        show = _SHOW_TIME_RE.search(rest) or _ANY_TIME_RE.search(rest)
        start = _local(date.group(1), show.group(1) if show else None)
        if start:
            return start
    return None


def _card(title_el):
    """The nearest ancestor that holds this show's date and no other show."""
    node = title_el
    for _ in range(5):
        node = node.parent
        if node is None:
            return None
        if len(node.select(".show-title")) > 1:
            return None
        if node.select_one("[itemprop=startDate], .time-show"):
            return node
    return None


def parse(html: str, *, venue: str) -> list[RawEvent]:
    """Show cards on an Another Planet venue page → RawEvents. Pure."""
    soup = BeautifulSoup(html, "html.parser")
    # Posters sit in their own link to the show page, apart from the card.
    posters: dict[str, str] = {}
    for a in soup.select("a[href]"):
        img = a.find("img")
        if img is not None and img.get("src"):
            posters.setdefault(a["href"], img["src"])
    events: list[RawEvent] = []
    seen: set[tuple[str | None, datetime]] = set()
    for title_el in soup.select(".show-title"):
        card = _card(title_el)
        if card is None:
            continue
        start = _card_start(card)
        title = re.sub(r"\s+", " ", title_el.get_text(" ", strip=True)).strip()
        if not (title and start):
            continue
        link = title_el.find_parent("a", href=True)
        url = link.get("href") if link else None
        if (url, start) in seen:
            continue
        seen.add((url, start))
        topline = card.select_one(".topline")
        support = card.select_one(".support")
        desc = " · ".join(t for t in (
            topline.get_text(" ", strip=True) if topline else "",
            f"with {support.get_text(' ', strip=True)}" if support and support.get_text(strip=True) else "",
        ) if t)
        img = card.find("img")
        image_url = img.get("src") if img is not None else posters.get(url or "")
        events.append(RawEvent(
            title=title,
            start_time=start,
            location=venue,
            url=url,
            description=desc or None,
            image_url=image_url,
        ))
    return sorted(events, key=lambda e: e.start_time)


def scrape_site(url: str, *, venue: str, tag: str = "anotherplanet") -> list[RawEvent]:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        print(f"[{tag}] blocked (HTTP {resp.status_code}) at {url}, skipping", flush=True)
        return []
    resp.raise_for_status()
    events = parse(resp.text, venue=venue)
    print(f"[{tag}] {len(events)} shows", flush=True)
    return events
