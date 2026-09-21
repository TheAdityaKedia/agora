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
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import RateLimited, browser_context, load_page_html


SOURCE = "atgtickets.com"
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


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    """Fetch and parse the ATG what's-on page."""
    with browser_context() as context:
        try:
            html = load_page_html(context, url, wait_until=WAIT_UNTIL, settle_ms=SETTLE_MS)
        except RateLimited as e:
            print(f"[atgtickets] blocked (HTTP {e.status}) at {e.url}, skipping", flush=True)
            return []
    return parse(html)
