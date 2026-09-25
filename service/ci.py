"""CI stage entry points for the per-source GitHub Actions pipeline.

`.github/workflows/scrape.yml` fans out one runner per source and fans back in
to a single DB writer — see feature-specs/ci-scraping.md. Each subcommand is
one stage:

  plan   — print the filtered source list as a JSON matrix
  scrape — scrape ONE url into a result file (no DB, no creds; always exits 0)
  merge  — save every result into the DB in sources.txt order, classify, export
  guard  — sanity-check the new manifest vs the base branch's, render the PR body

Everything goes through `pipeline.<fn>` (the main module) rather than
from-imports so tests can monkeypatch "main.<fn>".
"""
import argparse
import json
import os
from pathlib import Path

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
                  events_json_path=None) -> dict:
    """The single writer: save results in sources.txt order, classify, export.

    Order matters — dedup attribution gives a shared row to the earlier source —
    so we walk sources.txt, not the artifacts. A source whose result is missing
    or failed saves nothing and keeps its rows from earlier runs.
    """
    pipeline.init_db()
    results = load_results(Path(results_dir))
    rows = []
    scraped_names: set[str] = set()
    for url in pipeline.select_urls(pipeline.load_sources(), source_filters, excludes):
        result = results.get(url, _MISSING)
        row = {"url": url, "name": result["source_name"], "status": result["status"],
               "events": len(result["events"]), "saved": 0, "merged": 0, "skipped": 0,
               "error": result["error"]}
        if result["status"] == "ok":
            try:
                events = [RawEvent.from_dict(d) for d in result["events"]]
                row["saved"], row["merged"], row["skipped"] = pipeline.save_events(
                    events, source=result["source_name"])
                scraped_names.add(result["source_name"])
            except Exception as e:
                row["status"] = "save_error"
                row["error"] = f"{type(e).__name__}: {e}"
        print(f"[{row['name'] or url}] {row['status']}: {row['saved']} saved, "
              f"{row['merged']} merged, {row['skipped']} skipped", flush=True)
        rows.append(row)

    # Same guard and scoping as main.run(): a classify failure (e.g. no AWS
    # creds) must not stop the export; a filtered run only tags what it scraped.
    if classify:
        scope = scraped_names if (source_filters or excludes) else None
        try:
            pipeline.classify_upcoming(source_names=scope)
        except Exception as e:
            print(f"[classify] skipped ({type(e).__name__}: {e})", flush=True)

    out = Path(events_json_path or os.environ.get("EVENTS_JSON_PATH", pipeline.DEFAULT_EVENTS_JSON))
    exported = pipeline.export_json(out)
    print(f"[export] wrote {exported} upcoming events to {out}", flush=True)
    return {"sources": rows, "exported": exported,
            "failed": sum(1 for r in rows if r["status"] != "ok")}


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
                               classify=not args.no_classify)
        args.report.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    cli()
