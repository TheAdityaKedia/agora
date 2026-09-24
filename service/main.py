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
    actsf, atgtickets, balboa, berkeleyrep, bigbrainbay, birdbeckett, blackbird,
    brava, citylights, cityarts, commonwealthclub, fillmore, fourstar, gamh,
    greatstar, greenapple, independent, magictheatre, medicinenightmares, nctcsf,
    neofuturists, palace, phoenix, presidio, riptide, sfbarguide, sfjazz, sfpl,
    sfplayhouse, sfwarmemorial, warfield, ybca, zspace,
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
    sfpl, bigbrainbay, zspace, balboa, fourstar, birdbeckett, medicinenightmares,
    commonwealthclub, cityarts, riptide, sfbarguide,
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


def classify_upcoming(classifier=None, client=None, cache_path=None, log=print,
                      source_names=None):
    """Gather distinct upcoming shows from the DB and classify cache misses.

    A show is a distinct (source, title) keyed on the event's first source;
    many performances collapse to one classification. Returns (classified,
    cached). Isolated from `run()` so it can be tested with a fake classifier
    and moved to a scheduler/Celery task later.

    `source_names` (a set of source NAMEs) scopes classification to events from
    those sources — so a subset `--sources` run only classifies what it
    scraped, not the whole DB. None means all upcoming shows (full run /
    backfill).
    """
    import classify as _classify
    from classifications import Cache
    from exporters.json_export import EXPORT_TZ

    classifier = classifier or _classify.classify_show
    cache_path = cache_path or (Path(__file__).parent / "data" / "classifications.json")

    # Match the exporter's window: it keeps events from the START OF TODAY
    # (local), so classification must too — otherwise an event earlier today
    # (past `now` but still shown) exports untagged. Start-of-today Pacific → UTC.
    today_local = datetime.now(EXPORT_TZ).date()
    cutoff = datetime.combine(today_local, datetime.min.time(), tzinfo=EXPORT_TZ).astimezone(timezone.utc)
    session = get_session()
    try:
        rows = []
        for e in session.query(Event).filter(Event.start_time >= cutoff).all():
            srcs = e.sources or ["?"]
            if source_names is not None and not (set(srcs) & source_names):
                continue
            rows.append((srcs[0], e.title, e.description))
    finally:
        session.close()

    shows = _classify.select_shows(rows)
    cache = Cache(cache_path)
    return _classify.classify_new_shows(shows, cache, classifier=classifier,
                                        client=client, log=log)


def run(
    source_filters: list[str] | None = None,
    max_workers: int | None = None,
    excludes: list[str] | None = None,
    classify: bool = True,
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
        failures = 0
        scraped_names: set[str] = set()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # Submit every scrape up front so they run concurrently, then walk
            # the futures in source order — .result() blocks on each in turn, so
            # saves happen in order while later scrapes proceed in the pool.
            #
            # Each source is isolated: a scraper that raises (markup changed,
            # site down, WAF block surfacing as an exception) is logged and
            # skipped, and the run continues. Without this, one bad source
            # aborts the whole run before the manifest is written — a fragility
            # that scales badly as the source list grows. The manifest is
            # rebuilt from the full DB regardless, so a failed source keeps its
            # last-scraped rows rather than vanishing.
            futures = [(url, pool.submit(_scrape_one, url)) for url in urls]
            for url, future in futures:
                try:
                    scraper, raw_events = future.result()
                    _save_scraped(scraper, raw_events, url)
                    if scraper is not None:
                        scraped_names.add(scraper.NAME)
                except Exception as e:
                    failures += 1
                    print(f"[error] {url} failed: {type(e).__name__}: {e}", flush=True)
        if failures:
            print(f"[run] {failures} of {len(urls)} sources failed (see above); "
                  f"manifest rebuilt from all surviving DB rows", flush=True)

    # Classify new shows before export so tags land in the manifest. Guarded:
    # a classification failure (e.g. no AWS creds locally) must not abort the
    # export — the manifest still ships, just without fresh tags.
    #
    # On a SUBSET run (a source allowlist/denylist was given) scope
    # classification to just the sources actually scraped this run; on a full
    # run leave it None so every uncached upcoming show gets classified.
    if classify:
        scope = scraped_names if (source_filters or excludes) else None
        try:
            classify_upcoming(source_names=scope)
        except Exception as e:
            print(f"[classify] skipped ({type(e).__name__}: {e})", flush=True)

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
    parser.add_argument(
        "--no-classify", action="store_true",
        help="Skip the AI tagging step (no Bedrock calls); still scrapes and "
             "exports. Use when AWS creds aren't available.",
    )
    args = parser.parse_args()
    run(source_filters=args.sources, excludes=args.exclude, max_workers=args.workers,
        classify=not args.no_classify)


if __name__ == "__main__":
    _cli()
