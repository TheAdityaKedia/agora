import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import LOOKAHEAD_DAYS
from db import init_db, get_session
from exporters.json_export import export_json
from models import Event
from scrapers import (
    actsf, atgtickets, berkeleyrep, blackbird, citylights, fillmore,
    gamh, greenapple, independent, sfjazz, ybca,
)
from scrapers.base import RawEvent

# Each scraper is a strategy module exposing matches(url), scrape(url), SOURCE.
# Dispatch picks the first whose matches() accepts the URL — add a source by
# writing its module and appending it here, no conditionals to edit.
SCRAPERS = [
    greenapple, citylights, blackbird, atgtickets, actsf,
    berkeleyrep, sfjazz, fillmore, gamh, ybca, independent,
]


SOURCES_FILE = Path(__file__).parent / "data" / "sources.txt"
# Where the static-site manifest is written after each run. Override with the
# EVENTS_JSON_PATH env var (e.g. GitHub Actions writes into the frontend dir).
DEFAULT_EVENTS_JSON = Path(__file__).parent / "data" / "events.json"


def load_sources() -> list[str]:
    return [line.strip() for line in SOURCES_FILE.read_text().splitlines() if line.strip()]


def _find_duplicate(session, raw: RawEvent) -> Event | None:
    """Return the existing Event that matches `raw`, or None.

    Order:
      1. Exact URL match (only when raw.url is not None — else `WHERE url IS
         NULL` would match every prior URL-less event).
      2. Same title + same start_time.
    """
    if raw.url is not None:
        existing = session.query(Event).filter_by(url=raw.url).first()
        if existing is not None:
            return existing
    return session.query(Event).filter_by(title=raw.title, start_time=raw.start_time).first()


def save_events(raw_events: list[RawEvent], source: str) -> tuple[int, int, int]:
    """Persist raw events, merging cross-source duplicates. Returns (saved, merged, skipped).

    - saved:   new rows inserted.
    - merged:  a duplicate matched an existing row and `source` was appended to
               its sources list (one physical event listed by multiple sources).
    - skipped: same-source re-scrapes, or events past the look-ahead horizon.
    """
    horizon = datetime.now(timezone.utc) + timedelta(days=LOOKAHEAD_DAYS)
    session = get_session()
    saved = merged = skipped = 0
    try:
        for raw in raw_events:
            if raw.start_time > horizon:
                skipped += 1
                continue
            existing = _find_duplicate(session, raw)
            if existing is not None:
                if source in (existing.sources or []):
                    # Same source re-scraping something it already produced.
                    skipped += 1
                    continue
                # SQLAlchemy's JSON column doesn't track in-place mutations,
                # so reassign a fresh list to trigger an UPDATE on commit.
                existing.sources = list(existing.sources) + [source]
                merged += 1
                continue
            session.add(Event(
                title=raw.title,
                start_time=raw.start_time,
                location=raw.location,
                url=raw.url,
                description=raw.description,
                image_url=raw.image_url,
                sources=[source],
                created_at=datetime.now(timezone.utc),
            ))
            saved += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return saved, merged, skipped


def scrape_and_save(url: str) -> None:
    for scraper in SCRAPERS:
        if scraper.matches(url):
            raw_events = scraper.scrape(url)
            saved, merged, skipped = save_events(raw_events, source=scraper.SOURCE)
            print(f"[{scraper.SOURCE}] {saved} saved, {merged} merged, {skipped} skipped")
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
