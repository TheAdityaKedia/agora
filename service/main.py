import argparse
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import LOOKAHEAD_DAYS
from db import init_db, get_session
from exporters.json_export import export_json
from models import Event
from scrapers import (
    actsf, atgtickets, berkeleyrep, blackbird, brava, citylights, fillmore,
    gamh, greatstar, greenapple, independent, magictheatre, nctcsf,
    neofuturists, palace, phoenix, presidio, sfjazz, sfpl, sfplayhouse,
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
    sfpl,
]


SOURCES_FILE = Path(__file__).parent / "data" / "sources.txt"
# Where the static-site manifest is written after each run. Override with the
# EVENTS_JSON_PATH env var (e.g. GitHub Actions writes into the frontend dir).
DEFAULT_EVENTS_JSON = Path(__file__).parent / "data" / "events.json"


def load_sources() -> list[str]:
    return [line.strip() for line in SOURCES_FILE.read_text().splitlines() if line.strip()]


def _find_duplicate(session, raw: RawEvent) -> Event | None:
    """Return the existing Event that matches `raw`, or None.

    A duplicate always shares the same start_time — sources that expose no
    per-performance URL (Berkeley Rep, NCTC, …) list many showings under one
    show URL, so URL alone can no longer identify an event.

    Order:
      1. Same URL + same start_time (only when raw.url is not None — else
         `WHERE url IS NULL` would match every prior URL-less event).
      2. Same title + same start_time (cross-source: one physical show at one
         time listed by two sources, possibly under different URLs).
    """
    if raw.url is not None:
        existing = session.query(Event).filter_by(url=raw.url, start_time=raw.start_time).first()
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


# Concurrent scrapes cap. Scrapes are I/O-bound (network + a headless browser
# subprocess), so threads give a real speedup; the ceiling bounds simultaneous
# Chromium instances (memory) and per-source rate-limit pressure. Override with
# the SCRAPER_WORKERS env var.
DEFAULT_WORKERS = 6


def _scrape_one(url: str):
    """Dispatch `url` to its scraper and return (scraper, raw_events).

    Returns (None, []) when no scraper matches. This is the slow, read-only,
    independent part of the pipeline — safe to run concurrently across sources.
    """
    for scraper in SCRAPERS:
        if scraper.matches(url):
            return scraper, scraper.scrape(url)
    return None, []


def _save_scraped(scraper, raw_events, url: str) -> None:
    if scraper is None:
        print(f"[warn] no scraper for {url}", flush=True)
        return
    # NAME is the human-readable label users see on the frontend. We persist it
    # directly (not the domain SOURCE) so the manifest, DB, and UI all agree on
    # one canonical string per venue and no frontend translation layer is needed.
    saved, merged, skipped = save_events(raw_events, source=scraper.NAME)
    # flush so per-source progress is visible live during a long run.
    print(f"[{scraper.NAME}] {saved} saved, {merged} merged, {skipped} skipped", flush=True)


def scrape_and_save(url: str) -> None:
    """Scrape one URL and persist it (single-URL, sequential path)."""
    scraper, raw_events = _scrape_one(url)
    _save_scraped(scraper, raw_events, url)


def run(
    source_filters: list[str] | None = None,
    max_workers: int | None = None,
    excludes: list[str] | None = None,
) -> None:
    """Scrape configured sources concurrently, then rewrite the JSON manifest.

    Scrapes run in a thread pool (I/O-bound), but events are SAVED serially in
    sources.txt order: dedup attribution depends on order (the earlier source
    wins a shared row, later ones merge), so saving in a fixed order keeps
    results deterministic regardless of which scrape finishes first. DB writes
    stay on the main thread (SQLAlchemy sessions aren't thread-safe).

    `source_filters` (allowlist) and `excludes` (denylist) are both substring
    matches against source URLs, and both can be combined: a URL runs when it
    matches the allowlist (or the allowlist is empty) AND matches none of the
    excludes. The manifest is still rebuilt from the full DB, so a subset run
    adds to the manifest without dropping events from sources that weren't
    scraped this time.
    """
    init_db()
    filters = source_filters or []
    exclude_terms = excludes or []
    urls = [
        u for u in load_sources()
        if (not filters or any(f in u for f in filters))
        and not any(x in u for x in exclude_terms)
    ]

    if urls:
        workers = max_workers or int(os.environ.get("SCRAPER_WORKERS", str(DEFAULT_WORKERS)))
        workers = max(1, min(workers, len(urls)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # Submit every scrape up front so they run concurrently, then walk
            # the futures in source order — .result() blocks on each in turn, so
            # saves happen in order while later scrapes proceed in the pool.
            futures = [(url, pool.submit(_scrape_one, url)) for url in urls]
            for url, future in futures:
                scraper, raw_events = future.result()
                _save_scraped(scraper, raw_events, url)

    out = Path(os.environ.get("EVENTS_JSON_PATH", DEFAULT_EVENTS_JSON))
    count = export_json(out)
    print(f"[export] wrote {count} upcoming events to {out}", flush=True)


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
    parser.add_argument(
        "--exclude", nargs="+", metavar="SUBSTRING", default=None,
        help="Skip sources whose URL contains any of these substrings. "
             "Applied after --sources, so both can be combined "
             "(e.g. --exclude sfjazz sfpl skips the slow/rate-limited sources).",
    )
    parser.add_argument(
        "--workers", type=int, default=None, metavar="N",
        help=f"Number of sources to scrape concurrently (default "
             f"SCRAPER_WORKERS env or {DEFAULT_WORKERS}).",
    )
    args = parser.parse_args()
    run(source_filters=args.sources, excludes=args.exclude, max_workers=args.workers)


if __name__ == "__main__":
    _cli()
