"""Pre-merge location check for a new or changed scraper.

``python -m places check --sources <substr>`` scrapes the matching sources
live and runs each distinct location string through the resolver against a
scratch copy of the venue files, so nothing committed changes. It prints
where every string would land: an existing venue, a new one from the map
(name, address, map link), or pending with the reason. Tests and
``places validate`` can't catch a string that resolves to nothing or to the
wrong building; this can. See CONTRIBUTING.md → "Check that every location
lands on the map".
"""
from __future__ import annotations

import shutil
import tempfile
from collections import Counter
from pathlib import Path

from .resolve import Resolver
from .store import DATA_DIR, Store


def check_locations(locations: Counter, source: str, store: Store, geocoder) -> list[dict]:
    """Resolve each string (text -> event count) into `store` and describe
    the outcome. Mutates `store` only; the caller decides whether to save."""
    resolver = Resolver(store, geocoder, retry_pending=True)
    rows = []
    for text, events in locations.most_common():
        out = resolver.resolve(text, [source], events)
        entry = out.entry or {}
        venue = store.venues.get(entry.get("venue", ""), {})
        if "pending" in entry:
            where = f"PENDING: {entry['pending']['reason']}"
        elif venue:
            where = f"{venue.get('name')}, {venue.get('address', '')}"
            if entry.get("room"):
                where += f" (room: {entry['room']})"
            if venue.get("lat") is not None:
                where += f" https://www.openstreetmap.org/?mlat={venue['lat']}&mlon={venue['lng']}#map=18/{venue['lat']}/{venue['lng']}"
        else:
            where = str(entry)
        rows.append({"text": text, "events": events, "action": out.action, "where": where,
                     "evidence": out.evidence})
    return rows


def format_rows(name: str, rows: list[dict]) -> list[str]:
    lines = [f"== {name}: {sum(r['events'] for r in rows)} events, {len(rows)} location(s)"]
    for r in rows:
        lines.append(f"  {r['action']:8} {r['events']:4}  {r['text']}")
        lines.append(f"           -> {r['where']}")
        if r["action"] in ("new", "alias") and r["evidence"]:
            lines.append(f"              {'; '.join(r['evidence'])}")
    return lines


def run(filters: list[str], data_dir: Path = DATA_DIR, geocoder=None, log=print) -> int:
    """Scrape the sources matching `filters` and print where their locations land."""
    import main  # the scraper registry; heavy, so imported only for this command
    from .geocode import Nominatim

    urls = main.select_urls(main.load_sources(), filters)
    if not urls:
        log(f"no source in sources.txt matches {filters}")
        return 1
    geocoder = geocoder or Nominatim()
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("venues.json", "venue_locations.json"):
            if (Path(data_dir) / name).exists():
                shutil.copy(Path(data_dir) / name, tmp)
        store = Store(Path(tmp))
        pending = 0
        for url in urls:
            scraper = main.find_scraper(url)
            if scraper is None:
                log(f"no scraper matches {url}")
                continue
            events = scraper.scrape(url)
            locations = Counter(e.location for e in events if e.location)
            rows = check_locations(locations, scraper.NAME, store, geocoder)
            pending += sum(r["action"] == "pending" for r in rows)
            for line in format_rows(scraper.NAME, rows):
                log(line)
    log(f"{pending} pending location(s). Check every 'new' and 'alias' line's map link "
        "points at the right building.")
    return 1 if pending else 0
