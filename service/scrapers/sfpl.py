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

    _log(f"done: {len(events)} events collected")
    return events
