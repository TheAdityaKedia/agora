"""ATG Tickets (San Francisco) events scraper.

ATG runs the Curran, Orpheum, and Golden Gate theatres. The what's-on page is
a Next.js/MUI single-page listing (~24 cards, no pagination), server-rendered
enough that Playwright's `wait_until="load"` gives us the hydrated DOM.
`networkidle` never fires because of persistent tracking beacons.

Each card is a show/run, not a single performance:
  - Single-day: "Fri, Sep 25, 2026"
  - Multi-day range: "Sat, Sep 26 - Sun, Sep 27, 2026"
  - Cross-year range: "Sat, Dec 30, 2026 - Sun, Jan 3, 2027"

Individual showtimes live on the detail page — we don't fetch them. We emit
one RawEvent per card, at 7:00 PM SF-local on the range's START day, with the
full displayed range in the description so users can click through for the
actual times.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import RateLimited, browser_context, load_page_html
from scrapers.performances import expand_shows


SOURCE = "atgtickets.com"
NAME = "ATG (Curran, Orpheum, Golden Gate)"
BASE_URL = "https://us.atgtickets.com"
EVENTS_URL = "https://us.atgtickets.com/whats-on/san-francisco/"
SOURCE_TZ = ZoneInfo("America/Los_Angeles")

# The page is React-rendered but the tracker beacons prevent networkidle from
# firing; `load` + a settle covers everything we need.
WAIT_UNTIL = "load"
SETTLE_MS = 4000

# Placeholder start-of-day time. ATG cards don't expose specific showtimes;
# most theater performances start in the evening, so 7pm SF-local is close
# enough for calendar sorting. Real showtimes are on the detail page.
DEFAULT_HOUR = 19  # 7 PM


def matches(url: str) -> bool:
    return "atgtickets.com" in url


# "Fri, Sep 25, 2026" or "Sat, Sep 26 - Sun, Sep 27, 2026" (year at the end)
# or "Sat, Dec 30, 2026 - Sun, Jan 3, 2027" (year on both sides).
_DAY_RE = r"[A-Za-z]{3},\s+[A-Za-z]{3}\s+\d{1,2}(?:,\s+\d{4})?"
_RANGE_RE = re.compile(rf"^({_DAY_RE})(?:\s+-\s+({_DAY_RE}))?$")


def _parse_day(text: str, fallback_year: int) -> date | None:
    """Parse 'Fri, Sep 25, 2026' or 'Fri, Sep 25' (using fallback_year)."""
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text.strip())
    for fmt in ("%a, %b %d, %Y", "%a, %b %d"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            if fmt == "%a, %b %d":
                parsed = parsed.replace(year=fallback_year)
            return parsed
        except ValueError:
            continue
    return None


def _parse_date_range(text: str) -> tuple[date, date] | None:
    """Return (start_date, end_date) from an ATG date string, or None."""
    text = re.sub(r"\s+", " ", text.strip())
    m = _RANGE_RE.match(text)
    if not m:
        return None
    left, right = m.group(1), m.group(2)
    # Year comes from the last day; the first may omit it.
    end_text = right or left
    end_date = _parse_day(end_text, datetime.now(SOURCE_TZ).year)
    if not end_date:
        return None
    if not right:
        return end_date, end_date
    start_date = _parse_day(left, end_date.year)
    if not start_date:
        return None
    # If a range's first date parsed to after the second, the omitted year on
    # the left was really the previous year (Dec 30 → Jan 3 case).
    if start_date > end_date:
        try:
            start_date = start_date.replace(year=start_date.year - 1)
        except ValueError:
            return None
    return start_date, end_date


def _card_url(card) -> str | None:
    a = card.find("a", href=True)
    return urljoin(BASE_URL, a["href"]) if a else None


def _card_image_url(card) -> str | None:
    """Return the card's poster image URL, or None.

    ATG cards use a `<picture>` with several `<source srcset>` variants and a
    fallback `<img src>`. The fallback is a good default (Cloudinary handles
    responsive sizing anyway) and dodges srcset parsing.
    """
    img = card.find("img")
    if img and img.get("src"):
        return urljoin(BASE_URL, img["src"])
    return None


def _card_paragraphs(card) -> list[str]:
    return [p.get_text(" ", strip=True) for p in card.find_all("p") if p.get_text(strip=True)]


def _parse_card(card) -> RawEvent | None:
    """Extract one RawEvent from a showCard block."""
    title_tag = card.find(["h2", "h3", "h4"])
    if not title_tag:
        return None
    title = title_tag.get_text(strip=True)

    paras = _card_paragraphs(card)
    if not paras:
        return None
    # The date <p> is the last one whose text parses as an ATG date range.
    date_range = None
    date_text = None
    for text in reversed(paras):
        parsed = _parse_date_range(text)
        if parsed:
            date_range = parsed
            date_text = text
            break
    if not date_range:
        return None
    # The venue <p> is the one immediately before the date; genre is first.
    date_idx = paras.index(date_text)
    venue = paras[date_idx - 1] if date_idx > 0 else None
    genre = paras[0] if paras else None
    # Subtitle: whatever sits between genre and venue, if anything.
    middle = paras[1:date_idx - 1] if date_idx > 1 else []
    subtitle = " · ".join(middle) if middle else None

    start_day, end_day = date_range
    start_time = datetime(start_day.year, start_day.month, start_day.day,
                          DEFAULT_HOUR, 0, tzinfo=SOURCE_TZ).astimezone(timezone.utc)

    description_bits = [genre]
    if subtitle:
        description_bits.append(subtitle)
    description_bits.append(date_text)  # preserve the displayed range
    description = " · ".join(b for b in description_bits if b)

    return RawEvent(
        title=title,
        start_time=start_time,
        location=venue,
        url=_card_url(card),
        description=description,
        image_url=_card_image_url(card),
    )


def parse(html: str) -> list[RawEvent]:
    """Parse ATG's what's-on page HTML into RawEvents (one per show)."""
    soup = BeautifulSoup(html, "html.parser")
    events: list[RawEvent] = []
    for card in soup.select('[data-testid="showCard"]'):
        ev = _parse_card(card)
        if ev is not None:
            events.append(ev)
    return events


def _find_theater_event(html: str) -> dict | None:
    """Return the show's schema.org TheaterEvent JSON-LD object, or None."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for obj in data if isinstance(data, list) else [data]:
            if isinstance(obj, dict) and obj.get("@type") == "TheaterEvent":
                return obj
    return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _location_str(node: dict) -> str | None:
    loc = node.get("location") or {}
    name = loc.get("name")
    addr = loc.get("address") or {}
    parts = [name, addr.get("streetAddress"), addr.get("addressLocality"), addr.get("postalCode")]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def parse_performances(html: str, *, show: RawEvent) -> list[RawEvent]:
    """Expand one show's detail page into one RawEvent per performance.

    ATG embeds a schema.org TheaterEvent whose `subEvent[]` lists every
    performance with an absolute `startDate` (tz-explicit — no year inference)
    and a per-performance ticket `offers.url`. A single-night show has no
    `subEvent`, so the top-level event itself is the one performance.
    """
    data = _find_theater_event(html)
    if not data:
        return []
    nodes = data.get("subEvent") or [data]
    events: list[RawEvent] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        start = _parse_iso(node.get("startDate"))
        if not start:
            continue
        url = (node.get("offers") or {}).get("url") or node.get("url") or show.url
        events.append(RawEvent(
            title=show.title,
            start_time=start.astimezone(timezone.utc),
            location=_location_str(node) or show.location,
            url=url,
            description=show.description,
            image_url=show.image_url,
        ))
    return events


def _scrape_show_performances(ctx, show: RawEvent) -> list[RawEvent]:
    """Render one show's detail page and parse its performances."""
    if not show.url:
        return []
    try:
        html = load_page_html(ctx, show.url, wait_until=WAIT_UNTIL, settle_ms=SETTLE_MS)
    except RateLimited as e:
        print(f"[atgtickets] blocked (HTTP {e.status}) at {e.url}, skipping performances", flush=True)
        return []
    return parse_performances(html, show=show)


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    """Fetch ATG's what's-on listing, then expand each show to its performances."""
    with browser_context() as context:
        try:
            html = load_page_html(context, url, wait_until=WAIT_UNTIL, settle_ms=SETTLE_MS)
        except RateLimited as e:
            print(f"[atgtickets] blocked (HTTP {e.status}) at {e.url}, skipping", flush=True)
            return []
    shows = parse(html)
    return expand_shows(shows, _scrape_show_performances, label="atgtickets")
