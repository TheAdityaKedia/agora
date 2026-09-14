import re
from datetime import datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.browser import RateLimited, browser_context, load_page_html


SOURCE = "citylights.com"
BASE_URL = "https://citylights.com"
EVENTS_URL = "https://citylights.com/events/"
# City Lights lists times in San Francisco local time. The displayed string
# carries a cosmetic "PST" suffix even during daylight time, so we ignore the
# literal abbreviation and localize the wall-clock time via zoneinfo (which
# applies the correct PST/PDT offset for the date).
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
STORE_ADDRESS = "City Lights Booksellers, 261 Columbus Ave, San Francisco, CA 94133"

# City Lights (Sucuri WAF + React-rendered content) needs a full render before
# the event list is present.
NETWORKIDLE = "networkidle"
SETTLE_MS = 2500

# Trailing timezone abbreviation on the date string, e.g. "... 7:00 pm PST".
_TZ_SUFFIX = re.compile(r"\s+[A-Z]{2,4}\s*$")


def matches(url: str) -> bool:
    return "citylights.com" in url


def _parse_datetime(text: str) -> datetime | None:
    """Parse 'Monday, September 14, 2026, 7:00 pm PST' → tz-aware UTC datetime."""
    cleaned = _TZ_SUFFIX.sub("", text.strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    try:
        naive = datetime.strptime(cleaned, "%A, %B %d, %Y, %I:%M %p")
    except ValueError:
        return None
    return naive.replace(tzinfo=SOURCE_TZ).astimezone(timezone.utc)


def _extract_description(block) -> str | None:
    """Pull the event blurb from a block.

    Newer events wrap it in a `data-testid="text-content"` module; older ones
    put it in a plain <p> after the title. Prefer the former, else fall back to
    the longest non-date paragraph.
    """
    tag = block.select_one("[data-testid='text-content']")
    if tag:
        text = tag.get_text(" ", strip=True)
        if text:
            return text
    content = block.select_one(".list-content")
    if not content:
        return None
    candidates = []
    for p in content.find_all("p"):
        if "shortcode-date" in (p.get("class") or []):
            continue
        text = p.get_text(" ", strip=True)
        if text:
            candidates.append(text)
    return max(candidates, key=len) if candidates else None


def parse(html: str) -> list[RawEvent]:
    """Parse City Lights' events-page HTML into RawEvents.

    Pure and browser-free so it can be unit-tested against fixture HTML.
    """
    soup = BeautifulSoup(html, "html.parser")

    events = []
    for block in soup.select(".list-item-block"):
        title_tag = block.select_one("h3.shortcode-title a")
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        event_url = urljoin(BASE_URL, title_tag["href"])

        date_tag = block.select_one("p.shortcode-date")
        if not date_tag:
            continue
        start_time = _parse_datetime(date_tag.get_text(strip=True))
        if not start_time:
            continue

        description = _extract_description(block)

        # "Virtual Event" vs in-store; there's no per-event street address.
        type_tag = block.select_one(".virtual-event-text")
        type_text = type_tag.get_text(" ", strip=True) if type_tag else ""
        location = "Virtual Event" if "virtual" in type_text.lower() else STORE_ADDRESS

        events.append(RawEvent(
            title=title,
            start_time=start_time,
            location=location,
            url=event_url,
            description=description,
        ))

    return events


def scrape(url: str = EVENTS_URL) -> list[RawEvent]:
    """Fetch and parse the City Lights events page.

    City Lights shows all upcoming events on a single page — no pagination —
    so this is one fetch. Dedupes within-run by event URL defensively.
    """
    seen_urls: set[str] = set()
    events: list[RawEvent] = []
    with browser_context() as context:
        try:
            html = load_page_html(context, url, wait_until=NETWORKIDLE, settle_ms=SETTLE_MS)
        except RateLimited as e:
            print(f"[citylights] blocked (HTTP {e.status}) at {e.url}, skipping", flush=True)
            return []
    for ev in parse(html):
        if ev.url in seen_urls:
            continue
        seen_urls.add(ev.url)
        events.append(ev)
    return events
