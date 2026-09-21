import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import LOOKAHEAD_DAYS
from db import init_db, get_session
from exporters.json_export import export_json
from models import Event
from scrapers import blackbird, citylights, greenapple
from scrapers.base import RawEvent

# Each scraper is a strategy module exposing matches(url), scrape(url), SOURCE.
# Dispatch picks the first whose matches() accepts the URL — add a source by
# writing its module and appending it here, no conditionals to edit.
SCRAPERS = [greenapple, citylights, blackbird]


SOURCES_FILE = Path(__file__).parent / "data" / "sources.txt"
# Where the static-site manifest is written after each run. Override with the
# EVENTS_JSON_PATH env var (e.g. GitHub Actions writes into the frontend dir).
DEFAULT_EVENTS_JSON = Path(__file__).parent / "data" / "events.json"


def load_sources() -> list[str]:
    return [line.strip() for line in SOURCES_FILE.read_text().splitlines() if line.strip()]


def _is_duplicate(session, raw: RawEvent) -> bool:
    # Primary: exact URL match — only when the incoming event actually has one.
    # SQLAlchemy translates filter_by(url=None) to `WHERE url IS NULL`, which
    # would match any prior URL-less event (from email/flyer/no-anchor sources
    # like Black Bird) and incorrectly reject every one of them as a dup.
    if raw.url is not None:
        if session.query(Event).filter_by(url=raw.url).first():
            return True
    # Secondary: same title + same start_time (catches email/screenshot
    # submissions of known events and dedupes URL-less sources against themselves).
    if session.query(Event).filter_by(title=raw.title, start_time=raw.start_time).first():
        return True
    return False


def save_events(raw_events: list[RawEvent], source: str) -> tuple[int, int]:
    """Persist raw events to the database, skipping duplicates and out-of-horizon events.

    Deduplicates by URL first, then by title + start_time.
    Also drops events whose start_time is past `now + LOOKAHEAD_DAYS` — belt
    and suspenders for scrapers that don't cap their own pagination.
    Returns (saved, skipped) counts.
    """
    horizon = datetime.now(timezone.utc) + timedelta(days=LOOKAHEAD_DAYS)
    session = get_session()
    saved = skipped = 0
    try:
        for raw in raw_events:
            if raw.start_time > horizon:
                skipped += 1
                continue
            if _is_duplicate(session, raw):
                skipped += 1
                continue
            session.add(Event(
                title=raw.title,
                start_time=raw.start_time,
                location=raw.location,
                url=raw.url,
                description=raw.description,
                source=source,
                created_at=datetime.now(timezone.utc),
            ))
            saved += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return saved, skipped


def scrape_and_save(url: str) -> None:
    for scraper in SCRAPERS:
        if scraper.matches(url):
            raw_events = scraper.scrape(url)
            saved, skipped = save_events(raw_events, source=scraper.SOURCE)
            print(f"[{scraper.SOURCE}] {saved} saved, {skipped} skipped")
            return
    print(f"[warn] no scraper for {url}")


def run():
    init_db()
    for url in load_sources():
        scrape_and_save(url)
    out = Path(os.environ.get("EVENTS_JSON_PATH", DEFAULT_EVENTS_JSON))
    count = export_json(out)
    print(f"[export] wrote {count} upcoming events to {out}")


if __name__ == "__main__":
    run()
