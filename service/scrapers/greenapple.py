import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Comment

from config import LOOKAHEAD_DAYS
from scrapers.base import RawEvent
from scrapers.browser import (
    RateLimited, browser_context, load_page_html, new_browser_context,
)


SOURCE = "greenapplebooks.com"
NAME = "Green Apple Books"
BASE_URL = "https://greenapplebooks.com"
# Green Apple lists times in local (San Francisco) time with no tz marker.
SOURCE_TZ = ZoneInfo("America/Los_Angeles")

# Delay between every fetch (listing months AND detail pages). Green Apple's
# robots.txt sets `crawl-delay: 10` for all agents, and the owner is OK with us
# scraping at that pace — so we honor it; don't shorten it. This is politeness,
# not the anti-bot fix: getting past Cloudflare is `full_chromium=True` plus the
# fresh-context retry (see scrape() and _resilient_fetcher()).
CRAWL_DELAY_S = 10
# Safety cap so pagination can't run away if a "Next Month" link ever loops.
MAX_PAGES = 24
# Green Apple offers "Next Month" links into perpetuity, so with a 365-day
# horizon a naive walk fetches ~13 pages — most of them empty, each costing a
# 10s crawl-delay. Stop once this many consecutive months come back with no
# events; a bookstore doesn't schedule past a months-long gap.
MAX_EMPTY_MONTHS = 2
# Sanity cap on phase-2 detail fetches. Green Apple has ~40 upcoming events, so
# this only bites if the listing balloons (or parse goes haywire): each fetch
# costs a 10s crawl-delay, so 80 is already ~13 min. Events past the cap keep
# their listing teaser.
MAX_DETAIL_FETCHES = 80


def _log(msg: str) -> None:
    print(f"[greenapple] {msg}", flush=True)


def matches(url: str) -> bool:
    return "greenapplebooks.com" in url


def _parse_datetime(date_str: str, time_str: str) -> datetime | None:
    # date_str: "Mon, 5/4/2026"  time_str: "7:00pm"
    try:
        date_part = date_str.split(", ", 1)[-1].strip()  # "5/4/2026"
        naive = datetime.strptime(f"{date_part} {time_str.strip()}", "%m/%d/%Y %I:%M%p")
        # Interpret as San Francisco local time, store canonical UTC.
        return naive.replace(tzinfo=SOURCE_TZ).astimezone(timezone.utc)
    except ValueError:
        return None


def _extract_detail(row, label: str) -> str | None:
    for item in row.select("div.event-list__details--item"):
        lbl = item.find("span", class_="event-list__details--label")
        if lbl and label in lbl.get_text():
            # remove the label span then return remaining text
            lbl.extract()
            return item.get_text(separator=" ", strip=True)
    return None


def parse(html: str) -> list[RawEvent]:
    """Parse Green Apple's events-page HTML into RawEvents.

    Pure and browser-free so it can be unit-tested against fixture HTML.
    """
    soup = BeautifulSoup(html, "html.parser")

    events = []
    for row in soup.select("div.views-row"):
        title_tag = row.find("h3", class_="event-list__title")
        if not title_tag:
            continue

        link = title_tag.find("a")
        if not link:
            continue

        title = link.get_text(strip=True)
        event_url = urljoin(BASE_URL, link["href"])

        date_str = _extract_detail(row, "Date:")
        time_str = _extract_detail(row, "Time:")

        description_tag = row.find("div", class_="event-list__body")
        description = description_tag.get_text(strip=True) if description_tag else None

        # Green Apple emits mobile+desktop <img> pairs with the same src; the
        # first hit is fine. The src is site-relative — resolve to absolute.
        img_tag = row.find("img")
        image_url = urljoin(BASE_URL, img_tag["src"]) if img_tag and img_tag.get("src") else None

        location_tag = row.find("address")
        location = location_tag.get_text(separator=", ", strip=True) if location_tag else None

        if not date_str or not time_str:
            continue

        start_time = _parse_datetime(date_str, time_str)
        if not start_time:
            continue

        events.append(RawEvent(
            title=title,
            start_time=start_time,
            location=location,
            url=event_url,
            description=description,
            image_url=image_url,
        ))

    return events


# Address parts to keep, in display order. The detail page's `p.address` also
# carries a `country` span ("United States") that adds nothing for a Bay Area
# calendar, and naively joining every span yields "San Francisco, ,, CA".
_ADDRESS_PARTS = ("address-line1", "address-line2", "locality",
                  "administrative-area", "postal-code")


def parse_detail_location(html: str) -> str | None:
    """Extract the venue address from a Green Apple event detail page.

    Green Apple's *listing* only emits an `<address>` for its own three stores,
    so "Offsite:" events come back with no location. The detail page always
    carries one, but as `p.address` inside `.event-details__location--location`
    — a `<p>` with class "address", NOT an `<address>` tag, which is easy to
    miss. Returns e.g. "Sydney Goldstein Theater, 275 Hayes St, San Francisco,
    CA 94102". Pure.
    """
    return _location_from_soup(BeautifulSoup(html, "html.parser"))


def _location_from_soup(soup) -> str | None:
    addr = soup.select_one(".event-details__location--location p.address") \
        or soup.select_one("p.address")
    if not addr:
        return None
    parts: list[str] = []
    for cls in _ADDRESS_PARTS:
        span = addr.find("span", class_=cls)
        if span:
            text = span.get_text(" ", strip=True)
            if text:
                parts.append(text)
    if not parts:
        return None
    # "CA" + "94102" read better joined by a space than a comma.
    tail = " ".join(parts[-2:]) if len(parts) >= 2 else ""
    head = parts[:-2] if tail else parts
    return ", ".join(head + ([tail] if tail else []))


# Tags that start a new paragraph when flattening the detail body to text.
_BLOCK_TAGS = ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
               "li", "ul", "ol", "blockquote")
# Every in-store detail page ends with the same store-logistics boilerplate: an
# "Accessibility" paragraph (step-free access) and a face-mask policy line.
# Useful on the page, noise in a calendar blurb (and to the tagger). The
# accessibility block is sometimes its own <p>, sometimes the tail of the real
# text's <p> after a <br><br> — so we strip at paragraph level after flattening,
# not by tag. "Free to Attend, Please RSVP" is kept: it's the cost signal.
_BOILERPLATE_HEADINGS = {"accessibility"}
_BOILERPLATE_LINE_RE = re.compile(r"\bface masks?\b|\bsurgical masks?\b", re.I)


def _body_text(body) -> str | None:
    """Flatten the detail body to text, one blank line between paragraphs.

    Source whitespace (template indentation, &nbsp;) is collapsed first, then
    <br> becomes a line break and block tags become paragraph breaks — so the
    frontend's `pre-wrap` full view shows the page's own paragraphing ("About
    the Readers" / bio / next bio) instead of one wall of text.
    """
    for c in body.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()
    for t in body.find_all(["script", "style"]):
        t.decompose()
    for s in body.find_all(string=True):
        s.replace_with(re.sub(r"\s+", " ", str(s)))
    for br in body.find_all("br"):
        br.replace_with("\n")
    for tag in body.find_all(_BLOCK_TAGS):
        tag.insert_before("\n\n")
        tag.insert_after("\n\n")

    paragraphs: list[list[str]] = [[]]
    for line in body.get_text().split("\n"):
        line = line.strip()
        if line:
            paragraphs[-1].append(line)
        elif paragraphs[-1]:
            paragraphs.append([])

    kept: list[str] = []
    for para in paragraphs:
        if not para or para[0].rstrip(":").lower() in _BOILERPLATE_HEADINGS:
            continue
        lines = [ln for ln in para if not _BOILERPLATE_LINE_RE.search(ln)]
        if lines:
            kept.append("\n".join(lines))
    return "\n\n".join(kept) or None


# Real event artwork lives under Drupal's public files dir. Anything else
# (the theme's ADA-compliance badge, the header logo) is site chrome.
_EVENT_IMAGE_RE = re.compile(
    r"^https://greenapplebooks\.com/sites/default/files/\S+\.(?:png|jpe?g|gif|webp)(?:\?\S*)?$",
    re.I)


def _is_event_image(url: str | None) -> bool:
    return bool(url and _EVENT_IMAGE_RE.match(url)
                and "gab-logo" not in url and "/styles/logo/" not in url)


def _image_from_soup(soup) -> str | None:
    """The event's own image, or None — never the site logo.

    Prefers the on-page `.event-details__image` <img> (Drupal's `event_image`
    style, 236x295 — plenty for the frontend's 72px thumb). Falls back to
    og:image, which is the full original upload but is *mangled* on this site:
    Drupal splits the image's alt text on commas into extra og:image tags like
    "https://greenapplebooks.comThursday", so each candidate is validated. A
    page with no event image yields None (seen on offsite and some in-store
    events); we never substitute the logo.
    """
    img = soup.select_one(".event-details__image img[src]")
    if img:
        src = urljoin(BASE_URL, img["src"])
        if _is_event_image(src):
            return src
    for meta in soup.select('meta[property="og:image"]'):
        content = (meta.get("content") or "").strip()
        if _is_event_image(content):
            return content
    return None


def parse_detail(html: str) -> dict:
    """Extract {description, location, image_url} from an event detail page.

    The listing's `event-list__body` is a ~200-char teaser ending in "..."; the
    full text lives in `.event-details__info--body`. Select that class exactly:
    the site-alert banner above it ("Welcome to our new website!...") shares the
    generic `aba-body` class. Any field the page lacks comes back None. Pure.
    """
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one(".event-details__info--body")
    return {
        "description": _body_text(body) if body else None,
        "location": _location_from_soup(soup),
        "image_url": _image_from_soup(soup),
    }


def _enrich_from_details(events: list[RawEvent], fetch) -> dict:
    """Fetch every event's detail page once and merge in what the listing lacks.

    One fetch per event (≈40, each behind the 10s crawl-delay, so ≈7 min):
      - description: the full body replaces the listing teaser, but only when
        it's non-empty and actually longer — never downgrade.
      - location: filled only when the listing had none ("Offsite:" rows); the
        listing's own store address is already right.
      - image: the detail page's event image when it has one; otherwise the
        listing's (possibly None) stays. Never invented.
    A failed page leaves that event's listing data intact and moves on; a
    persistent block (RateLimited after the fresh-context retry) stops the pass
    but keeps every event. Returns counts for the summary log.
    """
    stats = {"fetched": 0, "descriptions": 0, "locations": 0, "images": 0,
             "failed": 0, "blocked": False}
    targets = [e for e in events if e.url]
    if len(targets) > MAX_DETAIL_FETCHES:
        _log(f"phase 2: {len(targets)} events exceeds MAX_DETAIL_FETCHES="
             f"{MAX_DETAIL_FETCHES}; enriching only the first {MAX_DETAIL_FETCHES}")
        targets = targets[:MAX_DETAIL_FETCHES]
    if not targets:
        return stats
    _log(f"phase 2: fetching {len(targets)} detail pages "
         f"(~{len(targets) * CRAWL_DELAY_S // 60} min at {CRAWL_DELAY_S}s crawl-delay)")
    for i, ev in enumerate(targets, start=1):
        time.sleep(CRAWL_DELAY_S)
        try:
            detail = parse_detail(fetch(ev.url))
        except RateLimited as e:
            _log(f"phase 2: blocked (HTTP {e.status}) at {e.url}, stopping enrichment "
                 f"({len(targets) - i + 1} events keep listing data)")
            stats["blocked"] = True
            break
        except Exception as e:
            stats["failed"] += 1
            _log(f"phase 2: {i}/{len(targets)} {ev.url} failed: {type(e).__name__}: {e}")
            continue
        stats["fetched"] += 1

        changed: list[str] = []
        desc = detail["description"]
        if desc and len(desc) > len(ev.description or ""):
            changed.append(f"desc {len(ev.description or '')}→{len(desc)}")
            ev.description = desc  # RawEvent is a plain mutable dataclass
            stats["descriptions"] += 1
        if not ev.location and detail["location"]:
            ev.location = detail["location"]
            changed.append(f"venue {ev.location}")
            stats["locations"] += 1
        if detail["image_url"] and detail["image_url"] != ev.image_url:
            ev.image_url = detail["image_url"]
            changed.append("image")
            stats["images"] += 1
        _log(f"phase 2: {i}/{len(targets)} {ev.title[:40]!r} → "
             f"{', '.join(changed) or 'no change'}")
    return stats


def find_next_month_url(html: str) -> str | None:
    """Return the absolute URL of the 'Next Month' link on the page, or None."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        if a.get_text(strip=True) == "Next Month":
            return urljoin(BASE_URL, a["href"])
    return None


_MONTH_URL_RE = re.compile(r"/events/(\d{4})/(\d{1,2})/?$")


def _first_of_month_from_url(url: str) -> date | None:
    """Parse a Green Apple `/events/YYYY/MM` URL to the first day of that month."""
    m = _MONTH_URL_RE.search(url)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), 1)
    except ValueError:
        return None


def _resilient_fetcher(context):
    """Return `fetch(url) -> html` that survives one Cloudflare challenge.

    Cloudflare scores a *session*: after ~13 requests on one cookie jar it
    starts answering with a "Just a moment..." challenge (403), even at the
    10s crawl-delay — yet a brand-new context on the same IP passes at once.
    So on a 403 we swap in a fresh context and retry that URL once; a second
    403 propagates as RateLimited and the caller stops as before.
    """
    current = [context]

    def fetch(url: str) -> str:
        try:
            return load_page_html(current[0], url)
        except RateLimited:
            _log(f"challenged at {url}; retrying once in a fresh browser context")
            current[0] = new_browser_context(context.browser)
            return load_page_html(current[0], url)

    return fetch


def scrape(url: str, horizon: date | None = None) -> list[RawEvent]:
    """Walk Green Apple's month-paginated event listing from `url` forward.

    Follows the site's "Next Month" link, stopping when:
      - the next-month URL is past `horizon` (defaults to today + LOOKAHEAD_DAYS)
      - MAX_EMPTY_MONTHS consecutive months yield no events
      - there is no next-month link
      - we would revisit a page we've already seen (loop guard)
      - MAX_PAGES safety cap

    Deduplicates within-run by event URL so an event that appears on two
    adjacent months is counted once.
    """
    if horizon is None:
        horizon = date.today() + timedelta(days=LOOKAHEAD_DAYS)

    all_events: list[RawEvent] = []
    seen_urls: set[str] = set()
    visited_pages: set[str] = set()
    empty_months = 0
    listing_blocked = False

    # full_chromium: the default headless shell gets stuck on Cloudflare's
    # managed challenge inside the container; see browser_context().
    with browser_context(full_chromium=True) as context:
        fetch = _resilient_fetcher(context)
        current = url
        for _ in range(MAX_PAGES):
            if current in visited_pages:
                break
            visited_pages.add(current)
            try:
                html = fetch(current)
            except RateLimited as e:
                _log(f"blocked (HTTP {e.status}) at {e.url}, stopping early")
                listing_blocked = True
                break

            new_count = 0
            for ev in parse(html):
                if ev.url in seen_urls:
                    continue
                seen_urls.add(ev.url)
                all_events.append(ev)
                new_count += 1
            _log(f"{current}: {new_count} events ({len(all_events)} total)")

            # Bound the walk once the calendar runs dry.
            empty_months = 0 if new_count else empty_months + 1
            if empty_months >= MAX_EMPTY_MONTHS:
                _log(f"{empty_months} consecutive empty months, stopping")
                break

            nxt = find_next_month_url(html)
            if not nxt:
                break
            # Green Apple offers "Next Month" links into perpetuity, so the
            # horizon check is the real end condition.
            nxt_month = _first_of_month_from_url(nxt)
            if nxt_month and nxt_month > horizon:
                break
            time.sleep(CRAWL_DELAY_S)  # robots.txt crawl-delay; see CRAWL_DELAY_S
            current = nxt

        if listing_blocked:
            # Already blocked even after a fresh context: every detail fetch
            # would just burn 10s to get the same 403. Ship the listing data.
            _log("phase 2: skipped, listing walk was blocked")
            stats = {"fetched": 0, "descriptions": 0, "locations": 0,
                     "images": 0, "failed": 0, "blocked": True}
        else:
            stats = _enrich_from_details(all_events, fetch)

    still_missing = sum(1 for e in all_events if not e.location)
    _log(f"done: {len(all_events)} events from {len(visited_pages)} pages; "
         f"{stats['fetched']} detail pages read ({stats['failed']} failed"
         f"{', enrichment stopped by block' if stats['blocked'] else ''}); "
         f"{stats['descriptions']} full descriptions, {stats['locations']} locations, "
         f"{stats['images']} images enriched; {still_missing} still without a venue")
    return all_events
