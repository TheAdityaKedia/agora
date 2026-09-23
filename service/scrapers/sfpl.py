"""San Francisco Public Library events scraper.

SFPL publishes ~2100 upcoming events (through ~4 months out) on a paginated
Drupal listing at `/events`. Static server-rendered HTML — no headless
browser needed. Each `.event--teaser` article has:
  - .event__title a → title + relative detail URL
  - .date-display-range → "Monday, 9/21/2026, 4:00 - 7:00" (no AM/PM;
    inferred from start hour: 7-11 → AM, everything else → PM)
  - .event__location → branch name ("Main", "Sunset", "Bookmobiles / MOS")
  - .event__audience → "Adults" / "Babies, Toddlers or Preschoolers" / …
  - img[src] → thumbnail (site-relative → resolve)
Pagination: `?page=N`; the last page is discoverable from the "Last »" link
on any page. We walk pages sequentially until we've fetched them all or
we've gone past our LOOKAHEAD horizon.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from config import LOOKAHEAD_DAYS
from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA


SOURCE = "sfpl.org"
NAME = "San Francisco Public Library"
BASE_URL = "https://sfpl.org"
EVENTS_URL = "https://sfpl.org/events"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")

REQUEST_TIMEOUT = 25
BETWEEN_PAGE_DELAY_S = 0.5
MAX_PAGES = 120  # safety bound; last page is typically ~85
# Description enrichment: fetch each event's detail page after the listing walk
# and lift the real blurb from its `og:description` meta tag. SFPL is fast and
# unrate-limited, so we fan out with a thread pool for a ~5x speedup over
# sequential fetches.
DETAIL_WORKERS = 5
# Emit progress every N detail fetches (avoid one line per URL — that's 1000+
# lines for a full SFPL run).
DETAIL_LOG_EVERY = 50

# ~70% of SFPL programming is storytime / early-learning / school-age
# activities that dwarf the "what's happening tonight" calendar for adults.
# Skip events whose audiences are exclusively kid-only. Teens, all-ages,
# families, and adults programming stay.
_KID_ONLY_AUDIENCES = frozenset({
    "event--babies-toddlers-or-preschoolers",
    "event--elementary-school-age",
    "event--middle-school-age",
})
_ADULT_RELEVANT_AUDIENCES = frozenset({
    "event--adults",
    "event--teens",
    "event--all-ages",
    "event--families",
})


def _is_kid_only(card) -> bool:
    """True if the article's audience classes are all kid-only.

    Multi-audience events (e.g. one with `event--all-ages` + `event--children`)
    stay because at least one adult-relevant audience is present.
    """
    classes = set(card.get("class") or [])
    kid = classes & _KID_ONLY_AUDIENCES
    if not kid:
        return False
    adult = classes & _ADULT_RELEVANT_AUDIENCES
    return not adult


# SFPL titles follow a "Type: Name" convention (e.g. "Workshop: Sewing Basics").
# Some of those types aren't public *happenings* you'd browse a calendar for —
# they're by-appointment 1:1 services or administrivia. Drop them at scrape
# time so they never reach the manifest or the classifier (where they only
# produced garbage tags, since the taxonomy has no home for "book a 1:1 tech
# appointment"). Matched case-insensitively against the title's colon prefix.
#   - Tutorial: reserve-a-slot 1:1 help (tech, financial coaching, Book a
#     Librarian) — a service, not a scheduled program.
#   - Services: drop-in social-worker / benefits / assessment desks.
#   - Canceled / Postponed: not happening.
#   - Storytime / Early Learning: child programming (marked all-ages/family so
#     the audience filter misses it) — off-target for an adult cultural calendar.
_SKIP_TITLE_PREFIXES = frozenset({
    "tutorial", "services", "canceled", "cancelled", "postponed",
    "storytime", "early learning",
    # Spanish/Chinese equivalents SFPL uses for the same service categories.
    "教程",   # "tutorial"
})

# Some non-events aren't distinguishable by prefix (they hide under Presentation/
# Workshop/Celebration). Match these phrases anywhere in the title instead.
# Phrases (not bare words) to avoid dropping genuine talks — "A Career in
# Filmmaking" is vocational, but a bare "career" would over-match.
#   - career/job help: vocational programming, not cultural events.
#   - open house / SFPL staff: library operational/admin events.
_SKIP_TITLE_PHRASES = (
    "a career in", "careers in", "career coaching", "career fair",
    "job search", "job help", "job fair", "job readiness",
    "open house", "sfpl staff",
)


def _is_skipped_type(title: str) -> bool:
    """True if the title marks a non-event to drop at scrape time.

    Two signals: the `Type:` colon prefix (handles bracketed status markers like
    "(FULL) Tutorial: …"), and phrase matches anywhere in the title for
    categories that hide under a legit prefix (career/job help, open houses).
    """
    low = title.lower()
    if any(phrase in low for phrase in _SKIP_TITLE_PHRASES):
        return True
    if ":" not in title:
        return False
    prefix = title.split(":", 1)[0].strip().lower()
    # Take the last word so "(full) tutorial" / "full workshop" reduce to the
    # actual category word.
    last = prefix.replace("(", " ").replace(")", " ").split()
    candidate = last[-1] if last else prefix
    return (candidate in _SKIP_TITLE_PREFIXES or prefix in _SKIP_TITLE_PREFIXES)

_DATE_RE = re.compile(
    r"^(?P<day>[A-Za-z]+),\s*"
    r"(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4}),\s*"
    r"(?P<sh>\d{1,2}):(?P<sm>\d{2})"
)


def matches(url: str) -> bool:
    return "sfpl.org" in url


def _log(msg: str) -> None:
    print(f"[sfpl] {msg}", flush=True)


def _infer_ampm(hour_12: int) -> int:
    """Convert a bare 12-hour hour (from a card that omits AM/PM) to 24-hour.

    SFPL renders times without AM/PM. Library hours are ~9am-8pm, so:
      - 7 through 11 → AM (morning programming / storytimes)
      - 12 → PM (noon)
      - 1 through 6 → PM (afternoon/evening programs)
      - anything else falls back to PM
    """
    if 7 <= hour_12 <= 11:
        return hour_12
    if hour_12 == 12:
        return 12
    return hour_12 + 12


def _parse_start(date_text: str) -> datetime | None:
    m = _DATE_RE.match(" ".join(date_text.strip().split()))
    if not m:
        return None
    try:
        year, month, day = int(m["y"]), int(m["m"]), int(m["d"])
        hour = _infer_ampm(int(m["sh"]))
        minute = int(m["sm"])
        return datetime(year, month, day, hour, minute,
                        tzinfo=SOURCE_TZ).astimezone(timezone.utc)
    except ValueError:
        return None


def _parse_card(card) -> RawEvent | None:
    # Filter out kid-only audiences at scrape time so they never enter the DB.
    if _is_kid_only(card):
        return None
    title_a = card.select_one(".event__title a")
    if not title_a:
        return None
    title = title_a.get_text(" ", strip=True)
    if not title:
        return None
    # Drop non-event categories (1:1 service appointments, canceled, …).
    if _is_skipped_type(title):
        return None
    href = title_a.get("href")

    date_tag = card.select_one(".event__date")
    if not date_tag:
        return None
    date_text = date_tag.get_text(" ", strip=True)
    start_time = _parse_start(date_text)
    if not start_time:
        return None

    loc_tag = card.select_one(".event__location")
    branch = loc_tag.get_text(" ", strip=True) if loc_tag else None
    location = f"SFPL — {branch}" if branch else "SFPL"

    aud_tag = card.select_one(".event__audience")
    audience = aud_tag.get_text(" · ", strip=True) if aud_tag else None
    topics_tag = card.select_one(".event__topics")
    topics = topics_tag.get_text(" · ", strip=True) if topics_tag else None
    # Preserve the displayed date+time (which includes duration) in the
    # description; the frontend already shows start_time, so this adds context.
    description = " · ".join(b for b in (date_text, audience, topics) if b) or None

    img_tag = card.select_one(".event__image img") or card.find("img")
    image_url = None
    if img_tag and img_tag.get("src"):
        image_url = urljoin(BASE_URL, img_tag["src"])

    return RawEvent(
        title=title,
        start_time=start_time,
        location=location,
        url=urljoin(BASE_URL, href) if href else None,
        description=description,
        image_url=image_url,
    )


def parse(html: str) -> list[RawEvent]:
    soup = BeautifulSoup(html, "html.parser")
    return [ev for ev in (_parse_card(c) for c in soup.select(".event--teaser")) if ev is not None]


def parse_event_description(html: str) -> str | None:
    """Return the real blurb from an SFPL /events/<slug> detail page.

    SFPL emits the full program description in `<meta property="og:description">`
    (also mirrored in `<meta name="description">`). Falls back to None so the
    caller can keep the listing-page metadata as a description.
    """
    soup = BeautifulSoup(html, "html.parser")
    for attrs in (
        {"property": "og:description"},
        {"name": "description"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            text = tag["content"].strip()
            if text:
                return text
    return None


def _fetch_description(url: str) -> str | None:
    """Fetch a detail page and return its og:description, or None on any error."""
    try:
        resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None
        return parse_event_description(resp.text)
    except requests.RequestException:
        return None


def _last_page_number(html: str) -> int | None:
    """Extract the highest page index from the pagination's 'Last »' link."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        m = re.search(r"[?&]page=(\d+)", a["href"])
        if m and "last" in a.get_text(" ", strip=True).lower():
            return int(m.group(1))
    return None


def scrape(url: str = EVENTS_URL, horizon: date | None = None) -> list[RawEvent]:
    """Walk SFPL's paginated /events listing until we've seen every page or
    events start landing past the look-ahead horizon.
    """
    if horizon is None:
        horizon = date.today() + timedelta(days=LOOKAHEAD_DAYS)
    horizon_dt = datetime.combine(horizon, datetime.max.time(), tzinfo=timezone.utc)
    seen_urls: set[str] = set()
    events: list[RawEvent] = []
    last_page: int | None = None

    for page in range(MAX_PAGES):
        page_url = url if page == 0 else f"{url}?page={page}"
        _log(f"page {page}: fetching")
        t0 = time.monotonic()
        try:
            resp = requests.get(page_url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            _log(f"page {page}: request error {type(e).__name__}: {e}")
            break
        if resp.status_code in (403, 429):
            _log(f"page {page}: blocked (HTTP {resp.status_code}), stopping")
            break
        if resp.status_code != 200:
            _log(f"page {page}: HTTP {resp.status_code}, stopping")
            break

        if last_page is None:
            last_page = _last_page_number(resp.text)
            if last_page is not None:
                _log(f"pagination: last page is {last_page}")

        page_events = parse(resp.text)
        new = 0
        past_horizon = 0
        for ev in page_events:
            if ev.start_time > horizon_dt:
                past_horizon += 1
                continue
            if ev.url and ev.url in seen_urls:
                continue
            if ev.url:
                seen_urls.add(ev.url)
            events.append(ev)
            new += 1
        _log(f"page {page}: {new} new events, {past_horizon} past horizon "
             f"({len(page_events)} on page, {time.monotonic() - t0:.1f}s)")

        if not page_events:
            _log(f"page {page}: empty, stopping")
            break
        if last_page is not None and page >= last_page:
            _log(f"page {page}: reached last page, done")
            break
        time.sleep(BETWEEN_PAGE_DELAY_S)

    _log(f"listing walk done: {len(events)} events collected")

    # --- Phase 2: enrich descriptions from detail pages ---
    unique_urls = sorted({ev.url for ev in events if ev.url})
    _log(f"phase 2: fetching og:description for {len(unique_urls)} detail pages "
         f"({DETAIL_WORKERS} workers)")
    descriptions: dict[str, str] = {}
    t_phase = time.monotonic()
    completed = 0
    total = len(unique_urls)
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        futures = {pool.submit(_fetch_description, u): u for u in unique_urls}
        for fut in as_completed(futures):
            completed += 1
            url = futures[fut]
            desc = fut.result()  # _fetch_description swallows errors
            if desc:
                descriptions[url] = desc
            if completed % DETAIL_LOG_EVERY == 0 or completed == total:
                _log(f"detail {completed}/{total} fetched "
                     f"({len(descriptions)} with descriptions, "
                     f"{time.monotonic() - t_phase:.0f}s elapsed)")

    # Apply — keep the listing metadata as fallback for events we couldn't
    # enrich (fetch error, 404, missing meta tag).
    enriched = 0
    for ev in events:
        if ev.url and ev.url in descriptions:
            ev.description = descriptions[ev.url]
            enriched += 1
    _log(f"done: {len(events)} events, {enriched} with rich descriptions")
    return events
