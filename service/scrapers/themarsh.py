"""The Marsh events scraper (Ludus calendar + WordPress detail enrichment).

Showtimes come from the Ludus ticketing calendar (scrapers/ludus.py) — it has
the dates/venue but no synopsis or poster. The Marsh's own WordPress site has a
page per show with the real blurb and banner image.

We join the two on the **Ludus show id**, not the title: every calendar event's
ticket link is ``…ludus.com/show_page.php?show_id=<id>``, and each WP show page
embeds a "Buy Tickets" link to ``…ludus.com/<id>`` for the same show. Matching
on that id is exact — it sidesteps title mismatches (abbreviated slugs like
``unique-derique-fll-whimsical``) and co-presentation aliases ("LABA's Name
Game" ⇄ "Elissa Strauss Name Game") that fuzzy title matching gets wrong.

We discover WP pages from the sitemaps (newest-modified first, since a current
show's page was just updated) and stop once every calendar show id is resolved.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

from scrapers import ludus
from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "themarsh.org"
NAME = "The Marsh"
CALENDAR_URL = "https://themarsh.ludus.com/calendar"
FALLBACK_LOCATION = "The Marsh, San Francisco / Berkeley"
REQUEST_TIMEOUT = 25
DETAIL_WORKERS = 6
MAX_INDEX_PAGES = 160  # safety bound on WP pages fetched while resolving show ids

# Shows live under /shows_and_events/ (post-sitemap) and at the root
# (page-sitemap, e.g. /our-loving-companions/).
_SITEMAP_URLS = ("https://themarsh.org/post-sitemap.xml",
                 "https://themarsh.org/page-sitemap.xml")
# A sitemap <url> block's loc + lastmod (loc immediately precedes lastmod here).
_URL_RE = re.compile(
    r"<loc>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</loc>\s*"
    r"<lastmod>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</lastmod>", re.S)
# The Ludus show id in either the calendar's shareUrl or a WP page's ticket link:
#   ludus.com/show_page.php?show_id=200542218   |   ludus.com/200542218
_LUDUS_SID_RE = re.compile(r"ludus\.com/(?:show_page\.php\?show_id=)?(\d+)")
# Boilerplate paragraphs (box-office / address blocks) to drop from the blurb.
_BOILER = re.compile(r"box office|valencia street|boxoffice@|marsh youth|\(415\)|allston", re.I)
_IMG_BAD = re.compile(r"logo|/button-|click-for-tickets|cropped|-icon|favicon", re.I)
# Skip these shows entirely — recurring open-stage nights with no per-show page
# and little value as individual calendar entries.
_SKIP_RE = re.compile(r"monday night marsh", re.I)
# Marks where a recurring show's page switches from its evergreen series blurb
# to the *current edition's* lineup — which is only right for the next date.
_EDITION_RE = re.compile(
    r"\b(artist bio(graphy)?|featuring|special guest|this (month|week|tuesday)"
    r"|line ?-?up|tonight|our line up)\b", re.I)


def matches(url: str) -> bool:
    return "themarsh.org" in url or "themarsh.ludus.com" in url


def _show_id(url: str | None) -> str | None:
    m = _LUDUS_SID_RE.search(url or "")
    return m.group(1) if m else None


def _norm(title: str) -> str:
    """Collapse to comparable alphanumerics, dropping a trailing year — for the
    title-containment fallback when a page doesn't embed its Ludus show id."""
    s = re.sub(r"[^a-z0-9]", "", (title or "").lower())
    return re.sub(r"(19|20)\d{2}$", "", s)


def _norm_contains(want: str, norms: set[str]) -> bool:
    return bool(want and any(len(n) >= 6 and (n in want or want in n) for n in norms))


def parse_show_page(html: str) -> tuple[str, str | None, str | None]:
    """Return (title, description, image_url) from a WP show page. Pure."""
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:title")
    title = re.sub(r"\s*-\s*The Marsh\s*$", "", (og.get("content") if og else "") or "").strip()

    content = soup.select_one(".entry-content") or soup
    paras = []
    for p in content.find_all("p"):
        text = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
        if len(text) >= 25 and not _BOILER.search(text):
            paras.append(text)
    description = " ".join(paras) or None

    image_url = None
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if "wp-content/uploads" in src and not _IMG_BAD.search(src):
            image_url = src
            break
    return title, description, image_url


def _evergreen(description: str | None) -> str | None:
    """Drop a recurring show's edition-specific tail, keeping the series blurb."""
    if not description:
        return None
    m = _EDITION_RE.search(description)
    return (description[:m.start()].strip() or None) if m else description


def _fetch(url: str, session: requests.Session) -> str | None:
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.text
    except requests.RequestException:
        return None


def _sitemap_pages(session: requests.Session) -> list[str]:
    """Candidate WP page URLs, newest-modified first (excl. livestream archives).

    A current show's page was just updated, so ordering by lastmod lets us find
    every calendar show id after only a few fetches."""
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for sitemap in _SITEMAP_URLS:
        xml = _fetch(sitemap, session)
        if not xml:
            continue
        for loc, lastmod in _URL_RE.findall(xml):
            if "/marshstream/" in loc or loc in seen:
                continue
            seen.add(loc)
            entries.append((loc, lastmod))
    entries.sort(key=lambda e: e[1], reverse=True)
    return [loc for loc, _ in entries]


def _slug_norm(url: str) -> str:
    m = re.search(r"/([a-z0-9-]+)/?$", url)
    return _norm(m.group(1)) if m else ""


def _scan_show_pages(session: requests.Session, shows: list[tuple[str, str]]):
    """Scan WP pages (newest-modified first) and index them two ways:
    by embedded Ludus show id (exact) and by slug/title norms (containment
    fallback). Stop once every calendar show resolves by *either* — or the page
    budget is hit. `shows` is the distinct (show_id, title) list to resolve.

    Returns (id_index, title_index) where id_index maps show_id -> payload and
    title_index is a list of (norms, payload)."""
    id_index: dict[str, dict] = {}
    title_index: list[tuple[set[str], dict]] = []

    def resolved(sid: str, title: str) -> bool:
        want = _norm(title)
        return sid in id_index or any(_norm_contains(want, norms) for norms, _ in title_index)

    pages = _sitemap_pages(session)
    fetched = 0
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        for i in range(0, len(pages), DETAIL_WORKERS):
            chunk = pages[i:i + DETAIL_WORKERS]
            for url, html in zip(chunk, pool.map(lambda u: _fetch(u, session), chunk)):
                fetched += 1
                if not html:
                    continue
                title, desc, image = parse_show_page(html)
                payload = {"description": desc, "image_url": image, "url": url}
                for sid in set(_LUDUS_SID_RE.findall(html)):
                    id_index.setdefault(sid, payload)
                norms = {n for n in (_slug_norm(url), _norm(title)) if n}
                if norms:
                    title_index.append((norms, payload))
            if fetched >= MAX_INDEX_PAGES or all(resolved(s, t) for s, t in shows):
                break
    return id_index, title_index


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    events = [e for e in ludus.scrape_calendar(CALENDAR_URL, fallback_location=FALLBACK_LOCATION)
              if not _SKIP_RE.search(e.title)]
    if not events:
        return events

    # Group each show's performances by its Ludus show id (the show key).
    from collections import defaultdict
    by_show: dict[str, list[RawEvent]] = defaultdict(list)
    for e in events:
        sid = _show_id(e.url)
        if sid:
            by_show[sid].append(e)
    if not by_show:
        return events

    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    shows = [(sid, group[0].title) for sid, group in by_show.items()]
    id_index, title_index = _scan_show_pages(session, shows)
    if not (id_index or title_index):
        print("[themarsh] no WP show pages resolved; showtimes only", flush=True)
        return events

    def _match(sid: str, title: str) -> dict | None:
        # Primary: exact Ludus show-id join. Fallback: title/slug containment
        # (some pages don't embed a show-id link, e.g. Not Just Jazz).
        if sid in id_index:
            return id_index[sid]
        want = _norm(title)
        return next((p for norms, p in title_index if _norm_contains(want, norms)), None)

    enriched = 0
    for sid, group in by_show.items():
        payload = _match(sid, group[0].title)
        if not payload:
            continue
        group.sort(key=lambda e: e.start_time)
        evergreen = _evergreen(payload["description"])
        for i, e in enumerate(group):
            e.image_url = payload["image_url"]
            e.url = payload["url"] or e.url
            # The page's lineup reflects only the next edition, so the soonest
            # occurrence gets the full text; later ones get the series blurb.
            e.description = payload["description"] if i == 0 else evergreen
            enriched += 1
    print(f"[themarsh] enriched {enriched}/{len(events)} events", flush=True)
    return events
