"""The Marsh events scraper (Ludus calendar + WordPress detail enrichment).

Showtimes come from the Ludus ticketing calendar (scrapers/ludus.py) — it has
the dates/venue but no synopsis or poster. The Marsh's own WordPress site has a
page per show (``/shows_and_events/<slug>/``) with a real blurb and banner
image, so we build an index of those pages, match each show by title, and
enrich its events with the description, poster, and the (nicer) WP show URL.
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
HOME_URL = "https://themarsh.org/"
FALLBACK_LOCATION = "The Marsh, San Francisco / Berkeley"
REQUEST_TIMEOUT = 25
DETAIL_WORKERS = 5
_MATCH_THRESHOLD = 0.4  # min title-token overlap (Jaccard) to trust a WP match

_SHOW_URL_RE = re.compile(r"https://themarsh\.org/shows_and_events/([a-z0-9-]+)/")
# Utility / non-show pages under /shows_and_events/ to skip.
_NON_SHOW = re.compile(r"donate|gift|membership|pass|^class-|marshstream|risings?$|runs$|marsh-rising", re.I)
# Boilerplate paragraphs (box-office / address blocks) to drop from the blurb.
_BOILER = re.compile(r"box office|valencia street|boxoffice@|marsh youth|\(415\)|allston", re.I)
_IMG_BAD = re.compile(r"logo|/button-|click-for-tickets|cropped|-icon|favicon", re.I)
_STOPWORDS = {"the", "a", "an", "and", "of", "with", "at", "presents", "marsh", "2025", "2026"}


def matches(url: str) -> bool:
    return "themarsh.org" in url or "themarsh.ludus.com" in url


def _title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def _norm(title: str) -> str:
    """Collapse a title to comparable alphanumerics, dropping a trailing year.

    Handles compressed WP titles/slugs ("NotJustJazz" / "notjustjazz") vs the
    calendar's spaced, year-suffixed form ("Not Just Jazz 2026")."""
    s = re.sub(r"[^a-z0-9]", "", (title or "").lower())
    return re.sub(r"(19|20)\d{2}$", "", s)


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


def _best_match(title: str, index: list[tuple[set[str], set[str], dict]]) -> dict | None:
    """Match a calendar title to a WP show by token overlap, or — for compressed
    titles/slugs — by normalized-string containment."""
    want_tokens = _title_tokens(title)
    want_norm = _norm(title)
    best_score, best = 0.0, None
    for tokens, norms, payload in index:
        union = want_tokens | tokens
        score = len(want_tokens & tokens) / len(union) if union else 0.0
        # Containment on the normalized form catches spacing/year differences
        # that zero out token overlap (e.g. "notjustjazz" vs "Not Just Jazz 2026").
        if want_norm and any(len(n) >= 6 and (n in want_norm or want_norm in n) for n in norms):
            score = max(score, 1.0)
        if score > best_score:
            best_score, best = score, payload
    return best if best_score >= _MATCH_THRESHOLD else None


def _fetch(url: str, session: requests.Session) -> str | None:
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.text
    except requests.RequestException:
        return None


def _build_wp_index(session: requests.Session) -> list[tuple[set[str], set[str], dict]]:
    """Fetch The Marsh's show pages and index them by title tokens + normalized
    keys (from both the page title and its slug)."""
    home = _fetch(HOME_URL, session)
    if not home:
        return []
    slugs = {s for s in _SHOW_URL_RE.findall(home) if not _NON_SHOW.search(s)}
    index: list[tuple[set[str], set[str], dict]] = []
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        results = pool.map(lambda s: (s, _fetch(f"https://themarsh.org/shows_and_events/{s}/", session)), slugs)
        for slug, html in results:
            if not html:
                continue
            title, desc, image = parse_show_page(html)
            norms = {n for n in (_norm(title), _norm(slug)) if n}
            tokens = _title_tokens(title)
            if not (tokens or norms):
                continue
            index.append((tokens, norms, {
                "description": desc, "image_url": image,
                "url": f"https://themarsh.org/shows_and_events/{slug}/",
            }))
    return index


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    events = ludus.scrape_calendar(CALENDAR_URL, fallback_location=FALLBACK_LOCATION)
    if not events:
        return events

    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    index = _build_wp_index(session)
    if not index:
        print("[themarsh] no WP show pages found; showtimes only", flush=True)
        return events

    matched = {}  # title -> payload (matched once per distinct show)
    enriched = 0
    for e in events:
        if e.title not in matched:
            matched[e.title] = _best_match(e.title, index)
        payload = matched[e.title]
        if payload:
            e.description = payload["description"]
            e.image_url = payload["image_url"]
            e.url = payload["url"] or e.url
            enriched += 1
    print(f"[themarsh] enriched {enriched}/{len(events)} events from WP show pages", flush=True)
    return events
