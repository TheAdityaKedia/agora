"""Z Space (San Francisco) events scraper.

Z Space's own site is a Squarespace shell with no machine-readable calendar, but
ticketing runs through OvationTix (AudienceView) client 34231, whose storefront
is a thin SPA over a clean JSON API. Two endpoints give us everything:

- ``CalendarProductions`` — one entry per date, each listing its productions and
  their individual ``showtimes`` (performanceId, start time, cancelled/visible
  flags). This is our per-performance source, so a multi-night run becomes one
  event per showing without any date fabrication.
- ``Production?expandPerformances=summary`` — the production catalog, keyed by
  id, carrying the rich HTML ``description`` and the ``venue`` name.

We join the two on production id: the calendar drives which performances exist,
the catalog supplies the synopsis and venue. Both endpoints need only a
``clientId`` header, so no headless browser is required.

Images: OvationTix serves a poster per production that has a logo uploaded
(``logoFile``), but some don't. For those we fall back to the poster on Z
Space's own Squarespace homepage — each current show is an image-link block
linking to its page — matched to the production by title-token overlap.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import re
import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "zspace.org"
NAME = "Z Space"
CLIENT_ID = "34231"
API_BASE = "https://web.ovationtix.com/trs/api/rest"
CALENDAR_URL = f"{API_BASE}/CalendarProductions"
PRODUCTIONS_URL = f"{API_BASE}/Production?expandPerformances=summary"
# Public storefront landing page for a production — the show/info page, shared
# by every performance (start_time keeps each performance a distinct row).
PRODUCTION_URL = "https://ci.ovationtix.com/34231/production/{id}"
# Logo images are served from the same API by their numeric file id.
IMAGE_URL = f"{API_BASE}/ClientFile({{file}})"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 30

# Some productions have no logo uploaded to OvationTix, but Z Space's own
# Squarespace homepage carries a poster per current show (an image-link block
# → the show's page). We use those as a fallback image, matched by title.
HOMEPAGE_URL = "https://www.zspace.org/"
_TITLE_STOPWORDS = {
    "the", "a", "an", "and", "of", "z", "space", "present", "presents",
    "premiere", "edition", "with", "for", "in", "on", "at", "to",
}
_IMAGE_MATCH_THRESHOLD = 0.5  # min token-overlap (Jaccard) to trust a match

# The rich HTML shatters into many tiny inline fragments (each <b>/<a> is its
# own node), so line-level filtering is hopeless — we flatten to one string and
# truncate at the first schedule/logistics section header, which reliably marks
# the end of the synopsis. Everything after (dates, runtime, policies) is dropped.
_CUT_RE = re.compile(
    r"\b(schedule|preview[s]?|opening night|performances|show ?times"
    r"|run ?time|running time|age recommendation[s]?|content (notice|warning|advisory)"
    r"|accessibility|ticketing policies|box office|please note|dates?/?times?)\b",
    re.IGNORECASE,
)


def matches(url: str) -> bool:
    return "zspace.org" in url


def _clean_description(html: str | None, presenter: str | None) -> str | None:
    """Flatten the production HTML, cut trailing schedule/logistics blocks, and
    prepend the presenter label when the synopsis doesn't already state it."""
    text = ""
    if html:
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        text = re.sub(r"[﻿​\xa0]", " ", text)  # BOM / zero-width / nbsp
        text = re.sub(r"\s+", " ", text).strip()
        cut = _CUT_RE.search(text)
        if cut:
            text = text[:cut.start()].strip()
    presenter = (presenter or "").strip()
    if presenter and presenter.lower() not in text.lower():
        text = f"{presenter} · {text}" if text else presenter
    return text or None


def _parse_start(value: str | None) -> datetime | None:
    """Parse an OvationTix 'YYYY-MM-DD HH:MM' wall-clock time (SF local) to UTC."""
    if not value:
        return None
    try:
        naive = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=SOURCE_TZ).astimezone(timezone.utc)


def _index_productions(productions: list[dict]) -> dict[int, dict]:
    """Map production id -> {'venue', 'description', 'supertitle'} from the catalog."""
    index: dict[int, dict] = {}
    for p in productions or []:
        venue = p.get("venue") or {}
        index[p.get("id")] = {
            "venue": (venue.get("name") or "").strip() or None,
            "description": p.get("description"),
            "supertitle": p.get("supertitle"),
        }
    return index


def parse_events(calendar: list[dict], productions: list[dict]) -> list[RawEvent]:
    """Join the calendar (performances) with the catalog (descriptions/venue).

    Pure — no network — so it's testable against captured API responses. One
    RawEvent per visible, non-cancelled showtime.
    """
    catalog = _index_productions(productions)
    events: list[RawEvent] = []
    for day in calendar or []:
        for prod in day.get("productions") or []:
            pid = prod.get("productionId")
            detail = catalog.get(pid, {})
            title = prod.get("name") or detail.get("supertitle")
            presenter = prod.get("supertitle") or detail.get("supertitle")
            description = _clean_description(detail.get("description"), presenter)
            location = detail.get("venue") or NAME
            url = PRODUCTION_URL.format(id=pid) if pid is not None else None
            logo = prod.get("logoFile")
            image_url = IMAGE_URL.format(file=logo) if logo else None
            for st in prod.get("showtimes") or []:
                if st.get("isCancelled") or st.get("isVisible") is False:
                    continue
                start_time = _parse_start(st.get("performanceStartTime"))
                if not (title and start_time):
                    continue
                events.append(RawEvent(
                    title=title,
                    start_time=start_time,
                    location=location,
                    url=url,
                    description=description,
                    image_url=image_url,
                ))
    return events


def _title_tokens(title: str) -> set[str]:
    """Significant lowercase word tokens of a title, minus boilerplate."""
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w for w in words if w not in _TITLE_STOPWORDS}


def _parse_homepage_blocks(html: str) -> list[tuple[str, str]]:
    """Return (show_page_href, poster_image_url) for each Z Space show block.

    Pure. Filters the homepage's image-link blocks to internal show pages
    (dropping social/charity links, which reuse a shared icon)."""
    soup = BeautifulSoup(html, "html.parser")
    blocks: list[tuple[str, str]] = []
    for a in soup.select("a.sqs-block-image-link"):
        href = a.get("href") or ""
        if "zspace.org/" not in href or any(
            s in href for s in ("facebook", "twitter", "instagram", "youtube", "charitynavigator")
        ):
            continue
        img = a.find("img")
        src = (img.get("data-src") or img.get("src")) if img else None
        if href and src:
            blocks.append((href, src))
    return blocks


def _match_image(title: str, entries: list[tuple[set[str], str]]) -> str | None:
    """Best image whose title tokens overlap `title` above the threshold, else None."""
    want = _title_tokens(title)
    if not want:
        return None
    best_score, best_img = 0.0, None
    for tokens, img in entries:
        union = want | tokens
        score = len(want & tokens) / len(union) if union else 0.0
        if score > best_score:
            best_score, best_img = score, img
    return best_img if best_score >= _IMAGE_MATCH_THRESHOLD else None


def _og_title(html: str) -> str | None:
    meta = BeautifulSoup(html, "html.parser").find("meta", property="og:title")
    return meta.get("content") if meta else None


def _venue_image_entries(session: requests.Session) -> list[tuple[set[str], str]]:
    """Fetch the homepage and each show block's page to build (title_tokens, image)."""
    try:
        home = session.get(HOMEPAGE_URL, timeout=REQUEST_TIMEOUT)
        home.raise_for_status()
    except requests.RequestException as e:
        print(f"[zspace] homepage fetch failed: {e}", flush=True)
        return []
    entries: list[tuple[set[str], str]] = []
    for href, img in _parse_homepage_blocks(home.text):
        try:
            page = session.get(href, timeout=REQUEST_TIMEOUT)
            title = _og_title(page.text) or ""
        except requests.RequestException:
            title = ""
        tokens = _title_tokens(title)
        if tokens:
            entries.append((tokens, img))
    return entries


def _fetch(url: str) -> list:
    resp = requests.get(
        url,
        headers={"clientId": CLIENT_ID, "Accept": "application/json", "User-Agent": BROWSER_UA},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    """Fetch the OvationTix calendar + production catalog and join them."""
    try:
        calendar = _fetch(CALENDAR_URL)
        productions = _fetch(PRODUCTIONS_URL)
    except (requests.RequestException, ValueError) as e:
        print(f"[zspace] API fetch failed: {e}", flush=True)
        return []
    events = parse_events(calendar, productions)

    # Fill images OvationTix lacks (some productions have no uploaded logo) from
    # Z Space's own homepage posters, matched by title.
    if any(e.image_url is None for e in events):
        session = requests.Session()
        session.headers.update({"User-Agent": BROWSER_UA})
        entries = _venue_image_entries(session)
        if entries:
            resolved: dict[str, str | None] = {}
            filled = 0
            for e in events:
                if e.image_url is not None:
                    continue
                if e.title not in resolved:
                    resolved[e.title] = _match_image(e.title, entries)
                if resolved[e.title]:
                    e.image_url = resolved[e.title]
                    filled += 1
            print(f"[zspace] filled {filled} images from venue homepage", flush=True)

    print(f"[zspace] done: {len(events)} events from {len(calendar)} calendar days", flush=True)
    return events
