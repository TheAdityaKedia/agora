import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import LOOKAHEAD_DAYS
from db import init_db, get_session
from exporters.json_export import export_json
from models import Event
from scrapers import (
    actsf, atgtickets, berkeleyrep, blackbird, brava, citylights, fillmore,
    gamh, greatstar, greenapple, independent, magictheatre, nctcsf,
    neofuturists, palace, phoenix, presidio, sfjazz, sfplayhouse,
    sfwarmemorial, warfield, ybca,
)
from scrapers.base import RawEvent

# Each scraper is a strategy module exposing matches(url), scrape(url), SOURCE.
# Dispatch picks the first whose matches() accepts the URL — add a source by
# writing its module and appending it here, no conditionals to edit.
SCRAPERS = [
    greenapple, citylights, blackbird, atgtickets, actsf,
    berkeleyrep, sfjazz, fillmore, gamh, ybca, independent,
    nctcsf, brava, magictheatre, greatstar, presidio,
    warfield, palace, sfwarmemorial, sfplayhouse, phoenix, neofuturists,
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


def run(source_filters: list[str] | None = None) -> None:
    """Scrape configured sources, then rewrite the JSON manifest.

    If `source_filters` is given, only sources whose URL contains any of the
    substrings run — the rest are skipped, but the manifest is still rebuilt
    from the full DB, so a subset run adds to the manifest without dropping
    events from sources that weren't scraped this time.
    """
    init_db()
    filters = source_filters or []
    for url in load_sources():
        if filters and not any(f in url for f in filters):
            continue
        scrape_and_save(url)
    out = Path(os.environ.get("EVENTS_JSON_PATH", DEFAULT_EVENTS_JSON))
    count = export_json(out)
    print(f"[export] wrote {count} upcoming events to {out}")


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape configured sources and refresh the JSON manifest.",
    )
    parser.add_argument(
        "--sources", nargs="+", metavar="SUBSTRING", default=None,
        help="Only scrape sources whose URL contains one of these substrings. "
             "Match is a plain substring, so 'gamh.com' or 'gamh' both work. "
             "Runs every source when omitted.",
    )
    args = parser.parse_args()
    run(source_filters=args.sources)


if __name__ == "__main__":
    _cli()
