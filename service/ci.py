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
from pathlib import Path

import main as pipeline


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


def cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Agora CI pipeline stages.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="Print the scrape matrix as JSON.")
    p.add_argument("--sources", nargs="*", default=[], metavar="SUBSTRING")
    p.add_argument("--exclude", nargs="*", default=[], metavar="SUBSTRING")

    s = sub.add_parser("scrape", help="Scrape one URL into a result file.")
    s.add_argument("--url", required=True)
    s.add_argument("--out", type=Path, required=True)

    args = parser.parse_args(argv)

    if args.cmd == "plan":
        print(json.dumps(plan_matrix(args.sources, args.exclude), separators=(",", ":")))
    elif args.cmd == "scrape":
        result = scrape_to_result(args.url)
        args.out.write_text(json.dumps(result))
        note = f" ({result['error']})" if result["error"] else ""
        print(f"[{result['source_name'] or args.url}] {result['status']}: "
              f"{len(result['events'])} events{note}", flush=True)


if __name__ == "__main__":
    cli()
