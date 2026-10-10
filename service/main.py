import argparse
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import dedup
import event_ids
import lifecycle
from config import LOOKAHEAD_DAYS
from db import init_db, get_session
from exporters.json_export import export_json
from models import Event
from scrapers import (
    actsf, alembic, atgtickets, balboa, berkeleyrep, bigbrainbay, birdbeckett,
    blackbird, brava, citylights, cityarts, commonwealthclub, elrio, factsf, fillmore,
    fourstar, frontiertower, gamh, greatstar, greenapple, independent, kronos,
    linesballet, litquake, magictheatre, medicinenightmares, missionfusion, nctcsf,
    neofuturists, oaklandartmurmur, palace, phoenix, presidio, readingrhythms,
    riptide, sfbarguide, sfjazz, sfpl, sfplayhouse, sfwarmemorial, sunsettrivia, themarsh,
    thecommons, warfield, ybca, zspace,
    bachdds, biscuitsblues, dawnclub, keysjazz, yoshis, partiful,
    booksmith, bookpassage, noevalleybooks, mrsdalloways, bookshopwestportal,
    clios, russianhill, booksinc, omnivore, fabulosa, tallyho, faight,
    atasite, ybgfestival, omca, jccsf, fortmason, sfmasonic, cobbs, punchline,
    glbthistory, sfcb, maritime, milibrary, oaklandtheaterproject, henryj,
    f8, diasporaarts, stanfordlive, castro, foxoakland, greekberkeley,
    bimbos, augusthall, feinsteins, calperformances, uctheatre, roxie, masala, drakes,
    paramount, oaklandlibrary, oacc, oaklandunited, eastsidearts, bayareafusion,
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
    commonwealthclub, cityarts, riptide, sfbarguide, sunsettrivia,
    thecommons, readingrhythms, oaklandartmurmur, elrio, kronos, litquake,
    themarsh, linesballet, factsf,
    yoshis, keysjazz, dawnclub, biscuitsblues, bachdds,
    booksmith, bookpassage, noevalleybooks, mrsdalloways, bookshopwestportal,
    clios, russianhill, booksinc, omnivore, fabulosa, tallyho,
    partiful, frontiertower, alembic, faight, missionfusion, masala,
    atasite, ybgfestival, omca, jccsf, fortmason, sfmasonic, cobbs, punchline,
    glbthistory, sfcb, maritime, milibrary, oaklandtheaterproject, henryj,
    f8, diasporaarts, stanfordlive, castro, foxoakland, greekberkeley,
    bimbos, augusthall, feinsteins, calperformances, uctheatre, roxie, drakes,
    paramount, oaklandlibrary, oacc, oaklandunited, eastsidearts, bayareafusion,
]


SOURCES_FILE = Path(__file__).parent / "data" / "sources.txt"
# Where the static-site manifest is written after each run. Override with the
# EVENTS_JSON_PATH env var (e.g. GitHub Actions writes into the frontend dir).
DEFAULT_EVENTS_JSON = Path(__file__).parent / "data" / "events.json"


def load_sources() -> list[str]:
    return [line.strip() for line in SOURCES_FILE.read_text().splitlines() if line.strip()]


_VENUE_OF = []


def _venue_of():
    """dedup's venue lookup from the committed venue files, loaded once per
    process (save time reads; only the merge job's resolve step writes)."""
    if not _VENUE_OF:
        try:
            from places.store import Store
            _VENUE_OF.append(dedup.venue_lookup(Store()))
        except Exception as e:  # unreadable files: text rules only
            print(f"[dedup] venue files not used ({type(e).__name__})", flush=True)
            _VENUE_OF.append(None)
    return _VENUE_OF[0]


def _find_duplicate(session, raw: RawEvent, source: str | None = None) -> Event | None:
    """Return the existing Event that matches `raw`, or None.

    A duplicate always shares the same start_time — sources that expose no
    per-performance URL (Berkeley Rep, NCTC, …) list many showings under one
    show URL, so URL alone can no longer identify an event.

    Order:
      1. Same URL + same start_time (only when raw.url is not None — else
         `WHERE url IS NULL` would match every prior URL-less event).
      2. Same title + same start_time (cross-source: one physical show at one
         time listed by two sources, possibly under different URLs).
      3. Cross-source fuzzy match (dedup.is_near_duplicate) against rows at
         the same start_time that `source` isn't already on — "Community
         Co-Working" vs "Alembic Community Co-Working". Never within one
         source: one source listing two similar titles at once means two events.
    """
    if raw.url is not None:
        existing = session.query(Event).filter_by(url=raw.url, start_time=raw.start_time).first()
        if existing is not None:
            return existing
    same_time = session.query(Event).filter_by(start_time=raw.start_time).all()
    for e in same_time:
        if e.title == raw.title:
            return e
    if source is not None:
        for e in same_time:
            if source not in (e.sources or []) and dedup.is_near_duplicate(
                    raw.title, raw.location, e.title, e.location, _venue_of()):
                return e
    return None


def _new_id(session, raw: RawEvent, source: str):
    """The stable id for a new row (event_ids.event_id). If a row already
    holds it (one whose url or title changed since, so it didn't match),
    fall back to a random id rather than fail the save."""
    eid = event_ids.event_id(source, raw.url, raw.title, raw.start_time)
    if session.get(Event, eid) is not None:
        print(f"[ids] {eid} already taken; using a random id for {raw.title[:60]!r}", flush=True)
        return uuid.uuid4()
    return eid


def save_events(raw_events: list[RawEvent], source: str,
                stats: dict | None = None) -> tuple[int, int, int]:
    """Persist raw events, merging cross-source duplicates. Returns (saved, merged, skipped).

    - saved:   new rows inserted (with their stable id, event_ids.event_id).
    - merged:  a duplicate matched an existing row and `source` was appended to
               its sources list (one physical event listed by multiple sources).
    - skipped: same-source re-scrapes with nothing new, or events past the
               look-ahead horizon.
    `stats["updated"]` (when given) counts same-source re-scrapes whose details
    or status changed and were updated (feature-specs/event-lifecycle.md, §2):
    only the row's creating source updates it; later sources only mark it seen.

    Every match and insert marks the row seen by `source` (lifecycle.mark_seen),
    which the CI merge reads to notice events that disappeared.
    """
    horizon = datetime.now(timezone.utc) + timedelta(days=LOOKAHEAD_DAYS)
    now = datetime.now(timezone.utc)
    session = get_session()
    saved = merged = skipped = updated = 0
    # Rows this batch already matched. One source listing two events with the
    # same title at the same time (two rooms, two screens) matches them both to
    # one row through _find_duplicate's title rule; without this the second
    # would apply_update over the first, and the next run would swap them back
    # — a row flapping its url/location forever, with a bogus "Venue changed"
    # badge and a manifest diff every refresh. The first one wins; the rest are
    # skipped, as they were before updates existed.
    claimed: set = set()
    collisions = 0
    try:
        for raw in raw_events:
            # "CANCELLED: …" → status, and the plain title for the id and matching.
            raw = lifecycle.with_title_status(raw)
            if raw.start_time > horizon:
                skipped += 1
                continue
            existing = _find_duplicate(session, raw, source)
            if existing is not None:
                was_moved = existing.status == lifecycle.MOVED
                lifecycle.mark_seen(existing, source, now)
                if was_moved and lifecycle.undo_move(session, existing):
                    print(f"[{source}] {existing.title[:60]!r} is listed again at its old "
                          f"time; undid the move", flush=True)
                if existing.id in claimed:
                    collisions += 1
                    skipped += 1
                    continue
                claimed.add(existing.id)
                if source in (existing.sources or []):
                    # Same source re-scraping something it already produced:
                    # the creating source's new details win.
                    if existing.sources[0] == source and lifecycle.apply_update(existing, raw, now):
                        updated += 1
                    else:
                        skipped += 1
                    continue
                # SQLAlchemy's JSON column doesn't track in-place mutations,
                # so reassign a fresh list to trigger an UPDATE on commit.
                existing.sources = list(existing.sources) + [source]
                merged += 1
                continue
            status = raw.status if raw.status in lifecycle.EXPLICIT else lifecycle.SCHEDULED
            new_id = _new_id(session, raw, source)
            claimed.add(new_id)  # a later event in this batch must not update it
            session.add(Event(
                id=new_id,
                title=raw.title,
                start_time=raw.start_time,
                location=raw.location,
                url=raw.url,
                description=raw.description,
                image_url=raw.image_url,
                sources=[source],
                created_at=now,
                status=status,
                status_at=now if status != lifecycle.SCHEDULED else None,
                seen={source: now.isoformat()},
                misses={source: 0},
            ))
            saved += 1
        if collisions:
            print(f"[{source}] {collisions} event(s) matched a row another event in this "
                  f"run already claimed (same title and time); kept the first", flush=True)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    if stats is not None:
        stats["updated"] = stats.get("updated", 0) + updated
    return saved, merged, skipped


# Concurrent scrapes cap. Scrapes are I/O-bound (network + a headless browser
# subprocess), so threads give a real speedup; the ceiling bounds simultaneous
# Chromium instances (memory) and per-source rate-limit pressure. Override with
# the SCRAPER_WORKERS env var.
DEFAULT_WORKERS = 6


def find_scraper(url: str):
    """Return the first scraper module whose matches() accepts `url`, or None."""
    for scraper in SCRAPERS:
        if scraper.matches(url):
            return scraper
    return None


def select_urls(urls: list[str], filters: list[str] | None = None,
                excludes: list[str] | None = None) -> list[str]:
    """Apply the --sources allowlist and --exclude denylist (plain substrings).

    A URL is kept when it matches the allowlist (or the allowlist is empty) AND
    matches none of the excludes. Order is preserved — it drives save order.
    """
    filters = filters or []
    excludes = excludes or []
    return [
        u for u in urls
        if (not filters or any(f in u for f in filters))
        and not any(x in u for x in excludes)
    ]


def _scrape_one(url: str):
    """Dispatch `url` to its scraper and return (scraper, raw_events).

    Returns (None, []) when no scraper matches. This is the slow, read-only,
    independent part of the pipeline — safe to run concurrently across sources.
    """
    scraper = find_scraper(url)
    if scraper is None:
        return None, []
    return scraper, scraper.scrape(url)


def _save_scraped(scraper, raw_events, url: str) -> None:
    if scraper is None:
        print(f"[warn] no scraper for {url}", flush=True)
        return
    # NAME is the human-readable label users see on the frontend. We persist it
    # directly (not the domain SOURCE) so the manifest, DB, and UI all agree on
    # one canonical string per venue and no frontend translation layer is needed.
    stats = {}
    saved, merged, skipped = save_events(raw_events, source=scraper.NAME, stats=stats)
    # flush so per-source progress is visible live during a long run.
    print(f"[{scraper.NAME}] {saved} saved, {merged} merged, {stats.get('updated', 0)} updated, "
          f"{skipped} skipped", flush=True)


def scrape_and_save(url: str) -> None:
    """Scrape one URL and persist it (single-URL, sequential path)."""
    scraper, raw_events = _scrape_one(url)
    _save_scraped(scraper, raw_events, url)


def classify_upcoming(classifier=None, client=None, cache_path=None, log=print,
                      source_names=None, time_budget_s=None, stats=None):
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
    budget = {} if time_budget_s is None else {"time_budget_s": time_budget_s}
    return _classify.classify_new_shows(shows, cache, classifier=classifier,
                                        client=client, log=log, stats=stats, **budget)


def _places_assistant():
    """The AI-assisted resolution step's model (classify's Haiku via Bedrock)."""
    import classify as _classify
    from places.assist import Assistant

    client = _classify.make_client()

    def ask(system: str, user: str) -> str:
        errors = []
        for model_id in _classify.available(_classify.MODELS):
            try:
                return _classify._converse(client, model_id, system, user)
            except Exception as e:  # try the next model
                _classify.note_failure(model_id, e)
                errors.append(f"{model_id}: {type(e).__name__}")
        raise RuntimeError("; ".join(errors))

    return Assistant(ask, profiles=_classify._source_profiles())


def resolve_places(geocoder=None, data_dir=None, max_lookups=None, log=print,
                   assistant="auto") -> dict:
    """Resolve upcoming events' location strings to venues (places/).

    Runs after classify, before export, over every upcoming event (not just
    this run's sources: a pending string can resolve on a later run). Saves
    the venue files; returns the summary for the run report. The exporter
    joins venues at export time, so nothing is written to event rows.
    """
    from exporters.json_export import EXPORT_TZ
    from places.geocode import Nominatim
    from places.pipeline import MAX_LOOKUPS, collect_locations, resolve_locations
    from places.store import DATA_DIR, Store

    today_local = datetime.now(EXPORT_TZ).date()
    cutoff = datetime.combine(today_local, datetime.min.time(), tzinfo=EXPORT_TZ).astimezone(timezone.utc)
    session = get_session()
    try:
        rows = [{"location": e.location, "sources": e.sources, "title": e.title, "url": e.url,
                 "start": e.start_time.astimezone(EXPORT_TZ).date().isoformat()}
                for e in session.query(Event).filter(Event.start_time >= cutoff).all()]
    finally:
        session.close()
    store = Store(data_dir or DATA_DIR)
    geocoder = geocoder or Nominatim(max_calls=max_lookups or MAX_LOOKUPS)
    if assistant == "auto":
        try:
            assistant = _places_assistant()
        except Exception as e:  # no boto3 / AWS config: resolve without it
            log(f"[places] AI step off ({type(e).__name__})")
            assistant = None
    summary = resolve_locations(collect_locations(rows), store, geocoder, today=today_local,
                                assistant=assistant)
    if "invalid" in summary:
        log(f"[places] skipped: venue files are invalid ({len(summary['invalid'])} problem(s))")
        return summary
    store.save()
    log(f"[places] {summary['actions']}, {summary['lookups']} lookups, {summary['ai_calls']} AI calls, "
        f"{len(summary['new_venues'])} new venue(s), {len(summary['pending'])} pending, "
        f"{summary['unresolved_events']}/{summary['events_with_location']} events unresolved")
    return summary


def run(
    source_filters: list[str] | None = None,
    max_workers: int | None = None,
    excludes: list[str] | None = None,
    classify: bool = True,
    places: bool = True,
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
    urls = select_urls(load_sources(), source_filters, excludes)

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

    # Resolve venues (OpenStreetMap lookups for new locations). Guarded like
    # classify: a failure (no network) must not stop the export, which joins
    # whatever the committed venue files already know.
    if places:
        try:
            resolve_places()
        except Exception as e:
            print(f"[places] skipped ({type(e).__name__}: {e})", flush=True)

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
    parser.add_argument(
        "--no-places", action="store_true",
        help="Skip venue resolution (no OpenStreetMap lookups); the export still "
             "joins the committed venue files.",
    )
    args = parser.parse_args()
    run(source_filters=args.sources, excludes=args.exclude, max_workers=args.workers,
        classify=not args.no_classify, places=not args.no_places)


if __name__ == "__main__":
    _cli()
