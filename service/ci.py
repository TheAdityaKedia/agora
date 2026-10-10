"""CI stage entry points for the per-source GitHub Actions pipeline.

`.github/workflows/scrape.yml` fans out one runner per source and fans back in
to a single DB writer (setup + ops: README.md → "Scheduled scraping"). Each
subcommand is one stage:

  plan   — print the filtered source list as a JSON matrix
  scrape — scrape ONE url into a result file (no DB, no creds; always exits 0)
  merge  — save every result into the DB in sources.txt order, classify, export
  guard  — sanity-check the new manifest vs the base branch's, render the PR body
  alert  — list failing sources (hard failures, 0 events) for the alert issue
  places — venue coverage + the places waiting for review, for the review issue

Everything goes through `pipeline.<fn>` (the main module) rather than
from-imports so tests can monkeypatch "main.<fn>".
"""
import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

import lifecycle
import main as pipeline
from scrapers.base import RawEvent


def plan_matrix(source_filters=None, excludes=None) -> list[dict]:
    """The scrape matrix: one entry per selected URL, in sources.txt order.

    `index` only exists to give each job's artifact a unique, valid name.
    """
    urls = pipeline.select_urls(pipeline.load_sources(), source_filters, excludes)
    return [{"index": i, "url": u} for i, u in enumerate(urls)]


def scrape_to_result(url: str) -> dict:
    """Scrape one URL and describe the outcome as a JSON-safe dict.

    Never raises: a scraper exception becomes status "error" so the CI job stays
    green and the merge job decides what the failure means.
    """
    result = {"url": url, "status": "ok", "source_name": None, "error": None, "events": []}
    scraper = pipeline.find_scraper(url)
    if scraper is None:
        result["status"] = "no_scraper"
        return result
    result["source_name"] = scraper.NAME
    try:
        events = scraper.scrape(url)
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"
        return result
    result["events"] = [e.to_dict() for e in events]
    return result


def load_results(results_dir: Path) -> dict[str, dict]:
    """Index every result file under `results_dir` by URL.

    download-artifact puts each artifact in its own subdirectory, hence rglob.
    A missing directory (every scrape job died) yields no results.
    """
    results = {}
    for path in sorted(Path(results_dir).rglob("*.json")):
        result = json.loads(path.read_text())
        results[result["url"]] = result
    return results


_MISSING = {"status": "missing", "source_name": None,
            "error": "no result file (job crashed or timed out)", "events": []}


def merge_results(results_dir, source_filters=None, excludes=None, classify=True,
                  events_json_path=None, resolve_places=True) -> dict:
    """The single writer: save results in sources.txt order, classify, resolve
    venues, export.

    Order matters — dedup attribution gives a shared row to the earlier source —
    so we walk sources.txt, not the artifacts. A source whose result is missing
    or failed saves nothing and keeps its rows from earlier runs.
    """
    pipeline.init_db()
    results = load_results(Path(results_dir))
    rows = []
    scraped_names: set[str] = set()
    run_start = datetime.now(timezone.utc)
    horizon = run_start + timedelta(days=pipeline.LOOKAHEAD_DAYS)
    returned: dict[str, list] = {}  # good sources → start times they listed (§3 window)
    bad_names: set[str] = set()
    for url in pipeline.select_urls(pipeline.load_sources(), source_filters, excludes):
        result = results.get(url, _MISSING)
        row = {"url": url, "name": result["source_name"], "status": result["status"],
               "events": len(result["events"]), "saved": 0, "merged": 0, "updated": 0,
               "skipped": 0, "error": result["error"]}
        if result["status"] == "ok":
            try:
                events = [RawEvent.from_dict(d) for d in result["events"]]
                stats = {}
                row["saved"], row["merged"], row["skipped"] = pipeline.save_events(
                    events, source=result["source_name"], stats=stats)
                row["updated"] = stats.get("updated", 0)
                scraped_names.add(result["source_name"])
                returned.setdefault(result["source_name"], []).extend(
                    e.start_time for e in events if e.start_time <= horizon)
            except Exception as e:
                row["status"] = "save_error"
                row["error"] = f"{type(e).__name__}: {e}"
        if row["status"] != "ok" and row["name"]:
            bad_names.add(row["name"])
        print(f"[{row['name'] or url}] {row['status']}: {row['saved']} saved, "
              f"{row['merged']} merged, {row['updated']} updated, {row['skipped']} skipped", flush=True)
        rows.append(row)

    # Disappearances (feature-specs/event-lifecycle.md, §3): only sources whose
    # every URL scraped well this run are judged.
    lifecycle_report = judge(returned, bad_names, run_start)
    for row in rows:
        row["possibly_partial"] = row["name"] in lifecycle_report["possibly_partial"]

    # Same guard and scoping as main.run(): a classify failure (e.g. no AWS
    # creds) must not stop the export; a filtered run only tags what it scraped.
    tagging = {}
    if classify:
        scope = scraped_names if (source_filters or excludes) else None
        try:
            pipeline.classify_upcoming(source_names=scope, stats=tagging)
        except Exception as e:
            tagging["error"] = f"{type(e).__name__}: {e}"
            print(f"[classify] skipped ({tagging['error']})", flush=True)

    # Venue resolution: never stops the export (see main.resolve_places).
    places = None
    if resolve_places:
        try:
            places = pipeline.resolve_places()
        except Exception as e:
            places = {"error": f"{type(e).__name__}: {e}"}
            print(f"[places] skipped ({places['error']})", flush=True)

    out = Path(events_json_path or os.environ.get("EVENTS_JSON_PATH", pipeline.DEFAULT_EVENTS_JSON))
    exported = pipeline.export_json(out)
    print(f"[export] wrote {exported} upcoming events to {out}", flush=True)
    return {"sources": rows, "exported": exported,
            "failed": sum(1 for r in rows if r["status"] != "ok"), "places": places,
            "classify": tagging or None,
            "lifecycle": {"updated": sum(r["updated"] for r in rows), **lifecycle_report}}


def judge(returned: dict, bad_names: set, run_start) -> dict:
    """lifecycle.judge_disappearances over the good sources; never stops the
    merge (a failure is reported and nothing is marked)."""
    good = {name: starts for name, starts in returned.items() if name not in bad_names}
    session = pipeline.get_session()
    try:
        out = lifecycle.judge_disappearances(session, good, run_start)
    except Exception as e:
        session.rollback()
        print(f"[lifecycle] skipped ({type(e).__name__}: {e})", flush=True)
        return {"unlisted": 0, "moved": 0, "possibly_partial": [], "error": f"{type(e).__name__}: {e}"}
    finally:
        session.close()
    print(f"[lifecycle] {out['unlisted']} no longer listed, {out['moved']} moved"
          + (f"; possibly partial: {', '.join(out['possibly_partial'])}" if out["possibly_partial"] else ""),
          flush=True)
    return out


# A re-tag run has the whole job to itself (no scraping), so it may tag for
# longer than the merge job's 15 minutes; retag.yml's timeout leaves room.
MAX_RETAG_MINUTES = 55


def retag_and_export(budget_minutes: float = 40, events_json_path=None) -> dict:
    """Re-tag stale or missing classifications, then export — no scraping.

    For catching up after a taxonomy bump (or a throttled run) without
    re-running every scraper. Reads upcoming events from the DB, tags within
    the budget, and re-exports with the committed venue files. A tagging
    failure (model unreachable) fails the run: unlike the scrape, there is
    nothing else to ship."""
    pipeline.init_db()
    minutes = max(1.0, min(float(budget_minutes), MAX_RETAG_MINUTES))
    tagging = {}
    pipeline.classify_upcoming(time_budget_s=minutes * 60, stats=tagging)
    out = Path(events_json_path or os.environ.get("EVENTS_JSON_PATH", pipeline.DEFAULT_EVENTS_JSON))
    exported = pipeline.export_json(out)
    print(f"[export] wrote {exported} upcoming events to {out}", flush=True)
    return {"kind": "retag", "sources": [], "exported": exported, "failed": 0,
            "places": None, "classify": tagging}


def _read_events_manifest(path) -> tuple[dict, int]:
    manifest = json.loads(Path(path).read_text())
    return manifest, len(manifest["events"])


def check_manifest(new_path, base_path, min_ratio: float = 0.7) -> dict:
    """Guard the new manifest against the base branch's before auto-merging.

    Blocks when the new manifest is unreadable or its event count fell below
    `min_ratio` of the base's. A missing/unreadable base (first run) passes.
    """
    try:
        new, count = _read_events_manifest(new_path)
    except (OSError, ValueError, KeyError, TypeError) as e:
        return {"passed": False, "changed": True, "count": 0, "base_count": 0,
                "reason": f"new manifest unreadable ({type(e).__name__}: {e})"}
    try:
        base, base_count = _read_events_manifest(base_path)
    except (OSError, ValueError, KeyError, TypeError):
        base, base_count = {}, 0
    # generated_at changes every run, so compare content, not bytes.
    changed = (new.get("events") != base.get("events")
               or new.get("taxonomy") != base.get("taxonomy"))
    passed = count >= min_ratio * base_count
    reason = None if passed else (
        f"event count dropped {base_count} → {count} (below {min_ratio:.0%} of base)")
    return {"passed": passed, "changed": changed, "count": count,
            "base_count": base_count, "reason": reason}


_STATUS_ICON = {"ok": "✅", "no_scraper": "⚠️", "error": "❌", "missing": "❌", "save_error": "❌"}


def render_pr_body(report: dict, guard: dict) -> str:
    """Markdown PR body: guard verdict + per-source table, so the merged PR
    history doubles as a scrape log."""
    verdict = "passed" if guard["passed"] else f"FAILED — {guard['reason']}"
    retag = report.get("kind") == "retag"
    lines = [
        "Automated re-tag (no scraping) from the retag workflow." if retag
        else "Automated refresh from the scheduled scrape workflow.",
        "",
        f"**Guard:** {verdict}",
        f"**Events:** {guard['base_count']} → {guard['count']}"
        + ("" if retag else f" · **Failed sources:** {report['failed']} of {len(report['sources'])}"),
    ]
    tagging = report.get("classify") or {}
    if "error" in tagging:
        lines.append(f"**Tagging:** skipped ({tagging['error'][:200]})")
    elif "classified" in tagging:
        lines.append(f"**Tagging:** {tagging['classified']} tagged in {tagging['seconds']}s · "
                     f"{tagging['failed']} failed · {tagging['left']} left for the next run · "
                     f"{tagging['throttled']} throttled retries")
    life = report.get("lifecycle")
    if life:
        partial = life.get("possibly_partial") or []
        lines.append(f"**Changes:** {life['updated']} updated · {life['unlisted']} no longer listed · "
                     f"{life['moved']} moved"
                     + (f" · ⚠️ possibly partial (misses not counted): {', '.join(partial)}" if partial else "")
                     + (f" · skipped ({life['error'][:200]})" if life.get("error") else ""))
    if not retag:
        lines += ["", "| Source | Status | Scraped | Saved | Merged | Updated | Skipped |",
                  "|---|---|---:|---:|---:|---:|---:|"]
    for r in report["sources"]:
        if r["status"] == "ok" and r["events"] == 0:
            status, icon = "ok (0 events)", "⚠️"
        elif r["status"] == "ok" and r.get("possibly_partial"):
            status, icon = "ok (possibly partial)", "⚠️"
        else:
            status, icon = r["status"], _STATUS_ICON.get(r["status"], "❔")
        lines.append(f"| {r['name'] or r['url']} | {icon} {status} | {r['events']} | "
                     f"{r['saved']} | {r['merged']} | {r.get('updated', 0)} | {r['skipped']} |")
    places = report.get("places") or {}
    if "actions" in places:
        lines += ["", f"**Venues:** {len(places['new_venues'])} new · "
                      f"{len(places['pending'])} waiting for review · "
                      f"{places['unresolved_events']} of {places['events_with_location']} "
                      f"events without a resolved location"]
    errors = [r for r in report["sources"] if r["error"]]
    if errors:
        lines += ["", "<details><summary>Errors</summary>", ""]
        for r in errors:
            error = r["error"].replace("`", "'").replace("\n", " ")[:500]
            lines.append(f"- **{r['name'] or r['url']}**: `{error}`")
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


LOCAL_ONLY_FILE = Path(__file__).parent / "data" / "local_only_sources.txt"


def load_local_only(path=LOCAL_ONLY_FILE) -> list[str]:
    """Substrings of sources known to fail from CI (scraped locally instead)."""
    entries = []
    for line in Path(path).read_text().splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            entries.append(entry)
    return entries


def find_alerts(report: dict, local_only: list[str]) -> list[dict]:
    """Sources worth an alert: any non-ok status, ok with 0 events, or a scrape
    the lifecycle judge found possibly partial.

    A possibly-partial source listed far fewer of its own events than the DB
    holds (lifecycle.PARTIAL_SHARE), so its misses weren't counted. That is the
    pipeline's only signal for a scraper that half-broke — it caught San
    Francisco Playhouse dropping 101 performances while both shows were still
    on its site — and it ships in an auto-merged PR body nobody reads, so it
    belongs here.

    Known local-only sources are skipped — they fail from CI every day by
    design, and alerting on them would bury real regressions.
    """
    alerts = []
    for r in report["sources"]:
        if any(entry in r["url"] for entry in local_only):
            continue
        if r["status"] != "ok":
            alerts.append({**r, "reason": r["status"]})
        elif r["events"] == 0:
            alerts.append({**r, "reason": "0 events"})
        elif r.get("possibly_partial"):
            alerts.append({**r, "reason": "possibly partial scrape (misses not counted)"})
    return alerts


def render_alert_body(alerts: list[dict], run_url: str, mention: str = "") -> str:
    """Markdown for the failure issue/comment: one line per failing source."""
    lines = [f"{len(alerts)} source(s) need attention in [this run]({run_url}). {mention}".rstrip(),
             "", "| Source | Problem | Error |", "|---|---|---|"]
    for a in alerts:
        error = (a["error"] or "").replace("`", "'").replace("|", "/").replace("\n", " ")[:300]
        lines.append(f"| {a['name'] or a['url']} | {a['reason']} | {error} |")
    lines += ["", "Existing rows for these sources are kept. A **possibly partial** scrape "
                  "listed far fewer events than the DB holds for it, so no event was marked "
                  "no longer listed — check the scraper before trusting a drop. Known "
                  "CI-blocked sources (`service/data/local_only_sources.txt`) are not "
                  "alerted on."]
    return "\n".join(lines) + "\n"


# The places check goes red (after data ships) when more than this share of
# upcoming events have an unresolved location, or more than MAX_NEW_PENDING
# strings go pending in one run (usually a source changed its location format).
UNRESOLVED_THRESHOLD = 0.03
MAX_NEW_PENDING = 20


def check_places(report: dict, store, threshold: float = UNRESOLVED_THRESHOLD,
                 max_new_pending: int = MAX_NEW_PENDING) -> dict:
    """Coverage and review-queue verdict from the merge report's "places"."""
    places = report.get("places")
    pending = {k: e["pending"] for k, e in store.locations.items() if "pending" in e}
    out = {"pending": pending, "new_pending": [], "problems": [], "alert": False,
           "share": None, "new_venues": []}
    if places is None:
        return out  # resolution not run (--no-places)
    if "error" in places:
        out["problems"].append(f"Venue resolution crashed: `{places['error'][:300]}`")
    elif "invalid" in places:
        out["problems"].append("The venue files don't validate, so this run shipped no areas:")
        out["problems"] += [f"  - {e}" for e in places["invalid"][:30]]
    else:
        out["new_pending"] = places["new_pending"]
        out["new_venues"] = places["new_venues"]
        total = places["events_with_location"]
        out["share"] = places["unresolved_events"] / total if total else 0.0
        if out["share"] > threshold:
            out["problems"].append(
                f"{places['unresolved_events']} of {total} upcoming events "
                f"({100 * out['share']:.1f}%) have an unresolved location (alert above "
                f"{100 * threshold:.0f}%).")
        if len(out["new_pending"]) > max_new_pending:
            out["problems"].append(
                f"{len(out['new_pending'])} location strings went pending in this run — "
                "usually a source changed how it writes locations.")
    out["alert"] = bool(out["problems"])
    return out


def render_places_body(check: dict, store, run_url: str) -> str:
    """Markdown for the "Places to review" issue."""
    lines = []
    if check["problems"]:
        lines += ["**Needs attention:**", ""] + [p if p.startswith("  ") else f"- {p}"
                                                  for p in check["problems"]] + [""]
    pending = sorted(check["pending"].items(), key=lambda kv: -kv[1].get("events", 0))
    share = "" if check["share"] is None else f" · {100 * (1 - check['share']):.1f}% of upcoming events have an area"
    lines.append(f"**{len(pending)} place(s) waiting for a decision**{share} · "
                 f"updated by [this run]({run_url})")
    for n, (key, p) in enumerate(pending, 1):
        lines += ["", *_pending_place(n, key, p, store)]
    if pending:
        repo = os.environ.get("GITHUB_REPOSITORY")
        file = (f"[`service/data/venue_locations.json`](https://github.com/{repo}/edit/main/"
                "service/data/venue_locations.json)" if repo else "`service/data/venue_locations.json`")
        lines += ["", "---", "", f"**How to answer:** open {file} in GitHub's editor, find the location "
                  "text, and replace its value (`{\"pending\": {…}}`) with one of:", "",
                  "- `{\"venue\": \"<id>\"}` — it's a venue in `service/data/venues.json`; add "
                  "`\"room\": \"…\"` for a room inside it. A new venue: add it to `venues.json` first.",
                  "- `{\"place\": \"online\"}` — an online event.",
                  "- `{\"place\": \"none\"}` — not a single place (a walking tour, \"TBA\").", "",
                  "Commit to `main`; the next run applies it and drops the place from this list. "
                  "Until then the event is on the site without an area or a map pin."]
    if check["new_venues"]:
        lines += ["", f"<details><summary>{len(check['new_venues'])} venue(s) added automatically in this run</summary>", ""]
        for vid in check["new_venues"]:
            v = store.venues.get(vid, {})
            where = (f" — [map](https://www.openstreetmap.org/?mlat={v['lat']}&mlon={v['lng']}"
                     f"#map=18/{v['lat']}/{v['lng']})" if v.get("lat") is not None else "")
            lines.append(f"- **{v.get('name', vid)}** ({v.get('region')}), {v.get('address', '')}{where}")
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def _pending_place(n: int, key: str, p: dict, store) -> list[str]:
    """One waiting place in the review issue: which event, why, and the
    likely answer when the text points at a venue we already have."""
    map_search = f"https://www.openstreetmap.org/search?query={quote_plus(key)}"
    heading = key.replace("`", "'")
    out = [f"### {n}. `{heading}`", ""]
    events = p.get("events", 0)
    more = f" and {events - 1} more" if events > 1 else ""
    ex = p.get("example")
    if ex:
        title = ex["title"].replace("[", "(").replace("]", ")")
        what = f"[{title}]({ex['url']})" if ex.get("url") else f"**{title}**"
        out.append(f"- **Event:** {what} on {ex['date']}{more}")
    else:
        out.append(f"- **Events:** {events} upcoming")
    out.append(f"- **From:** {', '.join(p.get('sources') or []) or 'unknown'} · "
               f"waiting since {p.get('first_seen', '?')}")
    out.append(f"- **Why it's waiting:** {p.get('reason', '')} ([search the map]({map_search}))")
    if p.get("suggestion"):
        out.append(f"- {_suggestion_text(p['suggestion'])}")
    for vid, room in _existing_venues(key, p, store)[:2]:
        v = store.venues[vid]
        answer = {"venue": vid, **({"room": room} if room else {})}
        out += [f"- **Probably {v['name']}**, a venue we already have (`{vid}`, {v.get('address', '')}). "
                "If so, the answer is:", "",
                "  ```json", f"  {json.dumps(answer)}", "  ```"]
    return out


_STREET_SUFFIX = {"st", "street", "ave", "avenue", "blvd", "boulevard", "rd", "road", "way", "dr",
                  "drive", "pl", "place", "ln", "lane", "ct", "court", "ter", "terrace", "hwy"}


def _street(segment: str) -> tuple[str, frozenset] | None:
    """'610 Old Mason St' → ('610', {'old', 'mason'})."""
    m = re.match(r"\s*(\d+[a-z]?)\s+([a-z0-9 .'-]+)$", segment.lower())
    if not m:
        return None
    words = frozenset(re.findall(r"[a-z0-9]+", m.group(2))) - _STREET_SUFFIX
    return (m.group(1), words) if words else None


def _existing_venues(key: str, p: dict, store) -> list[tuple[str, str | None]]:
    """Venues the pending text names, or whose street address it carries
    (same number, the venue's street words all present: '610 Old Mason St'
    finds '610 Mason Street'). Each with a room guess for a name match."""
    from places.normalize import GENERIC, name_tokens, names_match, normalize_key, venue_part
    sug = p.get("suggestion") or {}
    texts = [key, normalize_key(sug.get("name") or ""), (sug.get("street_address") or "").lower()]
    streets = {st for t in texts for seg in t.split(",") if (st := _street(seg))}
    found = []
    for vid, v in store.venues.items():
        name = normalize_key(v.get("name") or "")
        named = bool(name_tokens(name) - GENERIC) and any(
            re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", t) for t in texts if t)
        vst = next((st for seg in (v.get("address") or "").split(",") if (st := _street(seg))), None)
        at = vst is not None and any(st[0] == vst[0] and vst[1] <= st[1] for st in streets)
        if not (named or at):
            continue
        room = None
        if named:
            vp, rm = venue_part(sug.get("name") or key)
            room = vp if names_match(rm, v["name"]) else rm if names_match(vp, v["name"]) else None
            if room and room == room.lower():
                room = room.title()
        found.append((vid, room))
    return found


def _suggestion_text(s: dict) -> str:
    """The AI step's proposal for a pending place, for the review issue."""
    if s.get("kind") != "venue":
        return f"AI: {s.get('kind', 'unknown').replace('_', ' ')}"
    what = ", ".join(x for x in (s.get("name"), s.get("street_address"), s.get("city")) if x)
    out = f"AI suggests **{what}**"
    m = s.get("map")
    if m:
        out += (f" — map found [{m.get('label')}](https://www.openstreetmap.org/?mlat={m['lat']}"
                f"&mlon={m['lng']}#map=18/{m['lat']}/{m['lng']}) (`{m.get('osm')}`)")
    return out


def cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Agora CI pipeline stages.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="Print the scrape matrix as JSON.")
    p.add_argument("--sources", nargs="*", default=[], metavar="SUBSTRING")
    p.add_argument("--exclude", nargs="*", default=[], metavar="SUBSTRING")

    s = sub.add_parser("scrape", help="Scrape one URL into a result file.")
    s.add_argument("--url", required=True)
    s.add_argument("--out", type=Path, required=True)

    m = sub.add_parser("merge", help="Save results to the DB in order, classify, export.")
    m.add_argument("--dir", type=Path, required=True)
    m.add_argument("--report", type=Path, required=True)
    m.add_argument("--sources", nargs="*", default=[], metavar="SUBSTRING")
    m.add_argument("--exclude", nargs="*", default=[], metavar="SUBSTRING")
    m.add_argument("--no-classify", action="store_true")
    m.add_argument("--no-places", action="store_true")

    rt = sub.add_parser("retag", help="Re-tag stale/missing classifications and export (no scraping).")
    rt.add_argument("--report", type=Path, required=True)
    rt.add_argument("--budget-minutes", type=float, default=40)

    g = sub.add_parser("guard", help="Check the new manifest and render the PR body.")
    g.add_argument("--new", type=Path, required=True)
    g.add_argument("--base", type=Path, required=True)
    g.add_argument("--report", type=Path, required=True)
    g.add_argument("--body", type=Path, required=True)
    g.add_argument("--min-ratio", type=float, default=0.7)

    a = sub.add_parser("alert", help="List failing sources and render the alert body.")
    a.add_argument("--report", type=Path, required=True)
    a.add_argument("--local-only", type=Path, default=LOCAL_ONLY_FILE)
    a.add_argument("--body", type=Path, required=True)
    a.add_argument("--run-url", required=True)
    a.add_argument("--mention", default="")

    pl = sub.add_parser("places", help="Venue coverage and the review queue for the places issue.")
    pl.add_argument("--report", type=Path, required=True)
    pl.add_argument("--body", type=Path, required=True)
    pl.add_argument("--run-url", required=True)
    pl.add_argument("--data-dir", type=Path, default=None)

    args = parser.parse_args(argv)

    if args.cmd == "plan":
        print(json.dumps(plan_matrix(args.sources, args.exclude), separators=(",", ":")))
    elif args.cmd == "scrape":
        result = scrape_to_result(args.url)
        args.out.write_text(json.dumps(result))
        note = f" ({result['error']})" if result["error"] else ""
        print(f"[{result['source_name'] or args.url}] {result['status']}: "
              f"{len(result['events'])} events{note}", flush=True)
    elif args.cmd == "merge":
        report = merge_results(args.dir, args.sources or None, args.exclude or None,
                               classify=not args.no_classify,
                               resolve_places=not args.no_places)
        args.report.write_text(json.dumps(report, indent=2))
    elif args.cmd == "retag":
        report = retag_and_export(args.budget_minutes)
        args.report.write_text(json.dumps(report, indent=2))
    elif args.cmd == "guard":
        # stdout is appended to $GITHUB_OUTPUT, so print only key=value lines.
        guard = check_manifest(args.new, args.base, args.min_ratio)
        report = json.loads(args.report.read_text())
        args.body.write_text(render_pr_body(report, guard))
        print(f"passed={str(guard['passed']).lower()}")
        print(f"changed={str(guard['changed']).lower()}")
        print(f"count={guard['count']}")
    elif args.cmd == "alert":
        # stdout is appended to $GITHUB_OUTPUT, so print only key=value lines.
        report = json.loads(args.report.read_text())
        alerts = find_alerts(report, load_local_only(args.local_only))
        args.body.write_text(render_alert_body(alerts, args.run_url, args.mention))
        print(f"count={len(alerts)}")
    elif args.cmd == "places":
        # stdout is appended to $GITHUB_OUTPUT, so print only key=value lines.
        from places.store import DATA_DIR, Store
        store = Store(args.data_dir or DATA_DIR)
        report = json.loads(args.report.read_text())
        check = check_places(report, store)
        args.body.write_text(render_places_body(check, store, args.run_url))
        print(f"pending={len(check['pending'])}")
        print(f"new_pending={len(check['new_pending'])}")
        print(f"alert={str(check['alert']).lower()}")


if __name__ == "__main__":
    cli()
