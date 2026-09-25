"""SFJAZZ Center events scraper.

SFJAZZ's public site (sfjazz.org) is behind Cloudflare that 403s *every*
document request — even a real headless browser (307→403) — so the calendar
can't be scraped directly. But the site is an Umbraco build hosted by Adage
Technologies, and its calendar loads from a clean JSON API on the origin host,
which is NOT Cloudflare-fronted:

    https://sfjazz-redesign-stage.adagetech.net/ace-api/events/?startDate=…&endDate=…

Phase 1: one call returns the whole season (one item per performance —
multi-night runs are already split by date), no browser needed.

Phase 2 (descriptions): the API's `synopsis` is empty for every item, but the
same staging host serves each production's detail page (`viewDetailCtaUrl`,
e.g. `/tickets/productions/26-27/<slug>/`) with plain `requests`, no WAF.
Performances of one production share a page (Chris Botti: 8 nights, 1 page),
so we fetch each unique page ONCE, serially, `DETAIL_DELAY_S` apart, and copy
the parsed description onto every performance. See `parse_detail` for what we
take from the page. The staging robots.txt only disallows `/umbraco/`; we only
ever fetch `/tickets/...` paths.

CAVEAT — this is someone's *staging* server. It could vanish, start
rate-limiting, or lag/diverge from production content, and we can't check
parity (production 403s us). Enrichment degrades gracefully: a failed page
leaves its performances with the listing-level description (the API
`subtitle`, or none), and a run of consecutive failures stops phase 2 while
still returning every event. If the host goes away entirely, fall back to a
residential-proxy / CF-bypass fetch of the production calendar.

Image and detail URLs we *emit* point at production sfjazz.org (they load fine
in a user's browser); only our fetches go to staging. The API's `eventDate`
carries a wrong offset (-05:00), so we build the time from the display date +
time strings as Pacific.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from config import LOOKAHEAD_DAYS
from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "sfjazz.org"
NAME = "SFJAZZ Center"
BASE_URL = "https://www.sfjazz.org"  # for user-facing image + detail URLs
STAGING_BASE = "https://sfjazz-redesign-stage.adagetech.net"  # what we fetch
ACE_API = f"{STAGING_BASE}/ace-api/events/"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
VENUE = "SFJAZZ Center, 201 Franklin St, San Francisco, CA 94102"
REQUEST_TIMEOUT = 30

# Politeness between detail-page fetches. Staging robots.txt sets no
# crawl-delay, but it's a small staging box, not a CDN — be gentle and serial.
DETAIL_DELAY_S = 1
DETAIL_TIMEOUT = 20
# Safety cap on phase-2 fetches. A 365-day season is ~130 unique productions,
# so this only bites if the API balloons; productions past the cap keep their
# listing-level description.
MAX_DETAIL_FETCHES = 200
# If staging starts failing wholesale (down, rate-limiting), stop hammering it:
# after this many failures in a row, skip the rest of phase 2.
MAX_CONSECUTIVE_FAILURES = 5
DETAIL_LOG_EVERY = 20
# Only these paths are production detail pages; never touch /umbraco/
# (disallowed in robots.txt) or anything else the API might someday link.
DETAIL_PATH_PREFIX = "/tickets/"

# Rich-text paragraph classes that mark a label/heading ("ABOUT THIS SERIES",
# "PRICING", "PERSONNEL TBA") or a patron testimonial rather than body copy.
_LABEL_CLASS = re.compile(r"^(?:h\d-style|blockquote)$")
# Short editorial placeholders: "PERSONNEL COMING SOON!", "SONG LIST TBA",
# "Please check back for more information about these performances."
_PLACEHOLDER = re.compile(r"\b(?:tba|tbd|coming soon)\W*$|\bcheck back\b", re.IGNORECASE)
# Package/series upsell lines: "PART OF THE FAMILY-FRIENDLY CONCERT SERIES!",
# "Save 10% when you purchase 5 or more concerts together",
# "TWO-TICKET PACKAGES AVAILABLE". (Prices themselves are kept.)
_UPSELL = re.compile(r"^part of the\b.*\b(?:series|package)\b|^save \d+%"
                     r"|\bpackages? available\b", re.IGNORECASE)
_SHORT_LINE = 80
# Ticket/package purchase links. A line that *starts* with one is a CTA
# ("Purchase › Nov 13: 7PM & 8:30PM"), not copy; mid-sentence links are kept.
_PURCHASE_HREF = re.compile(r"/tickets/packages/|/smartseat/|/cart/")


def _log(msg: str) -> None:
    print(f"[sfjazz] {msg}", flush=True)


def matches(url: str) -> bool:
    return "sfjazz.org" in url


def _parse_start(date_str: str | None, time_str: str | None) -> datetime | None:
    """Build a UTC start from the API's display date + time (Pacific wall-clock).

    We use `eventDateString` ("10/1/2026") + `eventTimeString` ("9:30 PM") rather
    than `eventDate`, whose tz offset is wrong (-05:00)."""
    if not (date_str and time_str):
        return None
    try:
        naive = datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %I:%M %p")
    except ValueError:
        return None
    return naive.replace(tzinfo=SOURCE_TZ).astimezone(timezone.utc)


def _abs_url(path: str | None) -> str | None:
    return urljoin(BASE_URL, path) if path else None


def _norm(text: str) -> str:
    """Collapse whitespace (incl. &nbsp;) within lines, keep `\\n` line breaks,
    drop blank lines."""
    lines = (re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def _el_text(el) -> str:
    """Text of a tag with `<br>` as a line break. get_text() with no separator
    keeps the source's own spacing, so `<strong>Name</strong>, and` stays
    "Name, and" (a " " separator would give "Name , and")."""
    for br in el.find_all("br"):
        br.replace_with("\n")
    return _norm(el.get_text())


def _html_to_text(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    return _el_text(BeautifulSoup(raw, "html.parser")) or None


def _listing_description(item: dict) -> str | None:
    """Listing-level description: `synopsis` (empty for every item today) or
    the `subtitle` HTML ("with James Genus and Simon Phillips", "Ages 2 - 3.5").

    We deliberately don't synthesize a line from `artists`: it duplicates the
    page's Personnel block and is sometimes wrong (one production lists an
    artist who isn't on the bill)."""
    return _html_to_text(item.get("synopsis")) or _html_to_text(item.get("subtitle"))


def parse_events(items: list[dict]) -> list[RawEvent]:
    """Map ace-api event objects to RawEvents (one per performance). Pure."""
    events: list[RawEvent] = []
    for it in items or []:
        title = (it.get("name") or "").strip()
        start_time = _parse_start(it.get("eventDateString"), it.get("eventTimeString"))
        if not (title and start_time):
            continue
        room = (it.get("location") or "").strip()
        location = f"SFJAZZ Center — {room}" if room else VENUE
        events.append(RawEvent(
            title=title,
            start_time=start_time,
            location=location,
            url=_abs_url(it.get("viewDetailCtaUrl")),
            description=_listing_description(it),
            image_url=_abs_url(it.get("thumbnail")),
        ))
    return events


def _body_paragraphs(root) -> list[str]:
    """Body copy from every `div.wysiwyg-item div.rich-text` in the page.

    Umbraco block layouts vary: concerts put the bio in a
    `section.fiftyfifty` beside a photo; classes/series use `section.wysiwyg`
    (sometimes two columns: blurb + dates/pricing). Both wrap copy in
    `.wysiwyg-item > .rich-text`, which nav, footer, ticket cards, the
    Personnel/package callouts and the "You Might Also Enjoy" carousel never
    use. Within it we drop CTA buttons and purchase-link lines, heading-styled
    labels, testimonials and placeholder/upsell lines."""
    paras: list[str] = []
    for rt in root.select("div.wysiwyg-item div.rich-text"):
        for el in rt.find_all(["p", "ul", "ol"], recursive=False):
            if any(_LABEL_CLASS.match(c) for c in el.get("class") or []):
                continue
            for btn in el.find_all("a", class_=re.compile(r"^btn")):
                btn.decompose()
            ctas = {c for a in el.find_all("a", href=_PURCHASE_HREF) if (c := _norm(a.get_text()))}
            if el.name == "p":
                text = _el_text(el)
            else:
                text = "\n".join(t for li in el.find_all("li") if (t := _el_text(li)))
            if ctas:
                text = "\n".join(ln for ln in text.split("\n")
                                 if not any(ln.startswith(c) for c in ctas))
            if not text or len(text) <= _SHORT_LINE and (
                    _PLACEHOLDER.search(text) or _UPSELL.search(text)):
                continue
            if text not in paras:
                paras.append(text)
    return paras


def _personnel(root) -> str | None:
    """The lineup callout (`.fullwidthcta-content` headed "Personnel") as one
    line: "Personnel: Jahari Stampley (keyboards); D-Erania Stampley (piano,
    alto saxophone); Others TBA".

    Worth keeping: it's reliably identifiable by its heading, names every
    sideman (good for search/tagging), and is simply absent on pages without
    one. Entries are `<br>`-separated; the role is the `span.light`, the name
    is whatever precedes it (usually `.brand-red`/`<strong>`, but some pages
    leave it unmarked, or tuck the `<br>` inside the role span). ";" separates
    entries because roles contain commas. "Check back soon for personnel"
    placeholders are dropped, and a lineup that is nothing but placeholders
    ("Others TBA" alone) is omitted."""
    for box in root.select("div.fullwidthcta-content"):
        head = box.find(["h2", "h3"])
        if not head or head.get_text(strip=True).casefold() != "personnel":
            continue
        entries: list[str] = []
        for p in box.select(".rich-text p, .rich-text li"):
            for span in p.select(".light"):
                trailing_br = "\n" if span.find("br") else ""
                role = _norm(span.get_text(" ")).replace("\n", " ")
                span.replace_with(f"\x00{role}\x01{trailing_br}")
            for line in _el_text(p).split("\n"):
                m = re.match(r"^(.*?)\s*\x00(.*?)\x01\s*$", line)
                if m and m.group(1) and m.group(2):
                    entry = f"{m.group(1)} ({m.group(2)})"
                else:
                    entry = _norm(line.replace("\x00", " ").replace("\x01", " "))
                if entry and not re.search(r"check back|coming soon", entry, re.IGNORECASE):
                    entries.append(entry)
        if entries and not all(_PLACEHOLDER.search(e) for e in entries):
            return "Personnel: " + "; ".join(entries)
        return None
    return None


def _og_description(soup) -> str | None:
    for attrs in ({"property": "og:description"}, {"name": "description"}):
        tag = soup.find("meta", attrs=attrs)
        text = _norm(tag.get("content") or "") if tag else ""
        if text:
            return text
    return None


def parse_detail(html: str) -> str | None:
    """Description from a production detail page. Pure.

    Body paragraphs (bio/blurb, `\\n\\n`-separated) + the Personnel lineup as
    its own final paragraph. If the page has no body copy, fall back to the
    `og:description` summary (hand-written, but we've seen it copy-pasted from
    the wrong show, so it's only a fallback). None if nothing real is there."""
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("main") or soup
    paras = _body_paragraphs(root)
    if not paras:
        og = _og_description(soup)
        if og:
            paras = [og]
    lineup = _personnel(root)
    if lineup:
        paras.append(lineup)
    return "\n\n".join(paras) or None


def _combine(lead: str | None, body: str) -> str:
    """Keep the listing subtitle as a lead paragraph ("sings Carmen McRae's
    Carmen Sings Monk" under a bare "Tiffany Austin" title) unless the page
    already says it."""
    if not lead or lead.casefold() in body.casefold():
        return body
    return f"{lead}\n\n{body}"


def _detail_url(event_url: str | None) -> str | None:
    """The staging-host URL to fetch for a production's (production-host) URL,
    or None if it isn't a `/tickets/...` detail page."""
    if not event_url:
        return None
    parts = urlsplit(event_url)
    if not parts.path.startswith(DETAIL_PATH_PREFIX):
        return None
    return STAGING_BASE + parts.path + (f"?{parts.query}" if parts.query else "")


def _fetch_detail(session: requests.Session, url: str) -> str | None:
    try:
        resp = session.get(url, timeout=DETAIL_TIMEOUT)
    except requests.RequestException as e:
        _log(f"detail fetch failed {url}: {type(e).__name__}: {e}")
        return None
    if resp.status_code != 200:
        _log(f"detail fetch failed {url}: HTTP {resp.status_code}")
        return None
    return resp.text


def enrich_descriptions(events: list[RawEvent], session: requests.Session) -> dict:
    """Phase 2: fetch each unique production page once (serial, polite) and
    set the description on every performance that links to it. Mutates
    `events`; returns counts for logging."""
    groups: dict[str, list[RawEvent]] = {}
    for ev in events:
        u = _detail_url(ev.url)
        if u:
            groups.setdefault(u, []).append(ev)
    urls = list(groups)  # API order = date order, so a cap keeps the soonest
    if len(urls) > MAX_DETAIL_FETCHES:
        _log(f"phase 2: {len(urls)} pages exceeds MAX_DETAIL_FETCHES="
             f"{MAX_DETAIL_FETCHES}; fetching the first {MAX_DETAIL_FETCHES} only")
        urls = urls[:MAX_DETAIL_FETCHES]
    _log(f"phase 2: fetching {len(urls)} detail pages for {len(events)} performances "
         f"(serial, {DETAIL_DELAY_S}s apart)")

    stats = {"pages": len(urls), "fetched": 0, "failed": 0, "described": 0,
             "events_described": 0, "aborted": False}
    consecutive = 0
    t0 = time.monotonic()
    for i, url in enumerate(urls, 1):
        if i > 1:
            time.sleep(DETAIL_DELAY_S)
        html = _fetch_detail(session, url)
        if html is None:
            stats["failed"] += 1
            consecutive += 1
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                _log(f"phase 2: {consecutive} consecutive failures — staging host "
                     f"looks down; skipping the remaining {len(urls) - i} pages")
                stats["aborted"] = True
                break
        else:
            consecutive = 0
            stats["fetched"] += 1
            desc = parse_detail(html)
            if desc:
                stats["described"] += 1
                stats["events_described"] += len(groups[url])
                for ev in groups[url]:
                    ev.description = _combine(ev.description, desc)
        if i % DETAIL_LOG_EVERY == 0:
            _log(f"detail {i}/{len(urls)}: {stats['described']} with descriptions, "
                 f"{stats['failed']} failed ({time.monotonic() - t0:.0f}s)")
    return stats


def scrape(url: str = ACE_API, horizon: date | None = None) -> list[RawEvent]:
    """Fetch the full season from the Adage ace-api in one call, then enrich
    descriptions from each production's staging detail page."""
    today = date.today()
    end = horizon or (today + timedelta(days=LOOKAHEAD_DAYS))
    params = {"startDate": today.isoformat(), "endDate": end.isoformat()}
    session = requests.Session()
    session.headers["User-Agent"] = BROWSER_UA
    try:
        resp = session.get(ACE_API, params=params,
                           headers={"Accept": "application/json"},
                           timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        items = resp.json()
    except (requests.RequestException, ValueError) as e:
        _log(f"ace-api fetch failed: {type(e).__name__}: {e}")
        return []
    events = parse_events(items if isinstance(items, list) else [])
    _log(f"phase 1: {len(events)} performances from ace-api")

    t0 = time.monotonic()
    stats = enrich_descriptions(events, session)
    subtitle_only = sum(1 for e in events if e.description) - stats["events_described"]
    _log(f"done: {len(events)} events, {stats['events_described']} with descriptions "
         f"from {stats['described']} pages, {subtitle_only} with subtitle only "
         f"({stats['fetched']} fetched, {stats['failed']} failed"
         f"{', aborted' if stats['aborted'] else ''}, {time.monotonic() - t0:.0f}s)")
    return events
