# Scheduled Scraping on GitHub Actions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily + on-demand GitHub Actions pipeline that scrapes every source on its own runner, merges results into Neon Postgres through a single writer, classifies, exports `events.json`, and ships it via a guarded, auto-merged PR.

**Architecture:** A new `service/ci.py` exposes four stage subcommands (`plan`, `scrape`, `merge`, `guard`) that reuse `main.py`'s existing functions. `.github/workflows/scrape.yml` wires them: `plan` → matrix of `scrape` jobs (one per URL, unprivileged, result files as artifacts) → one `merge` job (DB + AWS + git permissions) that saves in `sources.txt` order, guards, opens a PR, merges it, and dispatches the Pages deploy.

**Tech Stack:** Python 3.13, SQLAlchemy/psycopg2, pytest, Playwright/Chromium, GitHub Actions (`actions/checkout@v4`, `setup-python@v5`, `cache@v4`, `upload-artifact@v4`, `download-artifact@v4`, `aws-actions/configure-aws-credentials@v4`), `gh` CLI, Neon Postgres.

**Spec:** `feature-specs/ci-scraping.md`

## Global Constraints

- One runner per source: matrix over the filtered `sources.txt`, `fail-fast: false`, `timeout-minutes: 15` per scrape job.
- Only the merge job writes to the DB, and it saves in `sources.txt` order.
- Scrape jobs receive only `ZYTE_API_KEY` — never `DATABASE_URL`, AWS creds, or write permissions.
- `ci.py scrape` always exits 0 and always writes a result file; scraper exceptions become `status: "error"`.
- Result statuses: `ok | error | no_scraper`; merge adds `missing` (no artifact) and `save_error`.
- Guard: manifest must parse; event count ≥ **0.7 ×** `main`'s count. Failed/zero-event sources do not block.
- Change detection ignores `generated_at` (it changes every run).
- Triggers: `schedule: "0 10 * * *"` + `workflow_dispatch` with optional `sources` input (substring filter, same semantics as `main.py --sources`).
- `concurrency: { group: scrape, cancel-in-progress: false }`.
- Ship branch `data/refresh-<run_id>`; commit subject `Refresh manifest: <N> events (<YYYY-MM-DD>)`; squash-merge; then `gh workflow run deploy-pages.yml`.
- Secrets: `DATABASE_URL` (Neon, `sslmode=require`), `AWS_ROLE_ARN`, `AWS_REGION`, `ZYTE_API_KEY`.
- Local Docker Compose flow (`main.py`) keeps working unchanged.
- Repo conventions: tests via `cd service && ./.venv/bin/python -m pytest`; imperative commit subjects, **no attribution footer**; never push to `main` directly.

## File Structure

| File | Responsibility |
|------|----------------|
| `service/scrapers/base.py` (modify) | `RawEvent.to_dict()` / `RawEvent.from_dict()` — JSON hand-off between processes |
| `service/main.py` (modify) | Extract `find_scraper(url)` and `select_urls(urls, filters, excludes)`; `_scrape_one` and `run()` use them |
| `service/ci.py` (create) | CI stage entry points: `plan_matrix`, `scrape_to_result`, `load_results`, `merge_results`, `check_manifest`, `render_pr_body`, `cli` |
| `service/tests/test_ci.py` (create) | Unit tests for everything in `ci.py` + `RawEvent` serialization |
| `service/tests/test_main.py` (modify) | Tests for `find_scraper`, `select_urls` |
| `.github/workflows/scrape.yml` (create) | The plan → scrape matrix → merge/ship workflow |
| `README.md`, `CLAUDE.md`, `DEVELOPMENT.md`, `feature-specs/ci-scraping.md` (modify) | Setup checklist, agent rules, design decision, spec correction |

## Task 0: Isolated workspace

The main checkout has unrelated uncommitted work (Green Apple/Zyte). Don't mix it in.

- [ ] **Step 1: Create a worktree on a feature branch from `main`**

```bash
cd /Users/kediaadi/workspace/LearningProjects/Agora
git worktree add ../Agora-ci -b ci-scraping main
cp feature-specs/ci-scraping.md feature-specs/ci-scraping-plan.md ../Agora-ci/feature-specs/
cd ../Agora-ci/service && python3.13 -m venv .venv && ./.venv/bin/pip install -q -r requirements.txt
./.venv/bin/python -m pytest -q
```
Expected: suite passes on the clean branch (baseline).

- [ ] **Step 2: Commit the spec + plan**

```bash
cd ../Agora-ci
git add feature-specs/ci-scraping.md feature-specs/ci-scraping-plan.md
git commit -m "Add spec and plan for per-source scraping on GitHub Actions"
```

All later paths are relative to `../Agora-ci`.

---

### Task 1: `RawEvent` JSON round-trip

**Files:**
- Modify: `service/scrapers/base.py`
- Create: `service/tests/test_ci.py`

**Interfaces:**
- Produces: `RawEvent.to_dict() -> dict` (all fields; `start_time` as ISO-8601 string with offset) and `RawEvent.from_dict(d: dict) -> RawEvent` (raises `ValueError` on a naive `start_time`).

- [ ] **Step 1: Write the failing tests** — create `service/tests/test_ci.py`:

```python
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch

from models import Base, Event
from scrapers.base import RawEvent


def _raw(title="Show", url="https://a.com/e/1", days=1) -> RawEvent:
    start = (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0)
    return RawEvent(title=title, start_time=start, location="Venue, San Francisco, CA",
                    url=url, description="desc", image_url=None)


# --- RawEvent serialization -------------------------------------------------

def test_rawevent_round_trips_through_json():
    raw = _raw()
    d = raw.to_dict()
    assert isinstance(d["start_time"], str)
    assert RawEvent.from_dict(json.loads(json.dumps(d))) == raw


def test_rawevent_round_trip_preserves_none_fields():
    raw = RawEvent(title="T", start_time=datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc),
                   location=None, url=None, description=None)
    assert RawEvent.from_dict(raw.to_dict()) == raw


def test_rawevent_from_dict_rejects_naive_start_time():
    with pytest.raises(ValueError):
        RawEvent.from_dict({"title": "T", "start_time": "2026-10-01T02:00:00",
                            "location": None, "url": None, "description": None,
                            "image_url": None})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd service && ./.venv/bin/python -m pytest tests/test_ci.py -v`
Expected: FAIL — `AttributeError: 'RawEvent' object has no attribute 'to_dict'`.

- [ ] **Step 3: Implement** — in `service/scrapers/base.py` change the import to `from dataclasses import asdict, dataclass` and add to `RawEvent`:

```python
    def to_dict(self) -> dict:
        """JSON-safe form, for handing events between processes (CI scrape → merge)."""
        d = asdict(self)
        d["start_time"] = self.start_time.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RawEvent":
        start = datetime.fromisoformat(d["start_time"])
        if start.tzinfo is None:
            raise ValueError(f"start_time must be timezone-aware: {d['start_time']!r}")
        return cls(**{**d, "start_time": start})
```

- [ ] **Step 4: Run to verify pass**

Run: `./.venv/bin/python -m pytest tests/test_ci.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add service/scrapers/base.py service/tests/test_ci.py
git commit -m "Add JSON round-trip to RawEvent"
```

---

### Task 2: Extract `find_scraper` and `select_urls` in `main.py`

**Files:**
- Modify: `service/main.py` (`_scrape_one`, and the URL filtering at the top of `run()`)
- Test: `service/tests/test_main.py`

**Interfaces:**
- Produces: `main.find_scraper(url: str) -> module | None`; `main.select_urls(urls: list[str], filters: list[str] | None = None, excludes: list[str] | None = None) -> list[str]` (allowlist AND not-denylist, plain substring, order preserved).

- [ ] **Step 1: Write the failing tests** — append to `service/tests/test_main.py` (and add `find_scraper, select_urls` to its `from main import ...` line):

```python
def test_select_urls_allowlist_and_denylist_compose():
    urls = ["https://sfpl.org/events", "https://sfplayhouse.org/", "https://gamh.com/calendar/"]
    # 'sfpl' also matches sfplayhouse (plain substring) — the denylist removes it.
    assert select_urls(urls, ["sfpl"], ["sfplayhouse"]) == ["https://sfpl.org/events"]
    assert select_urls(urls) == urls
    assert select_urls(urls, None, ["gamh"]) == urls[:2]


def test_find_scraper_returns_none_for_unknown_url():
    assert find_scraper("https://no-such-venue.example/") is None


def test_find_scraper_matches_known_source():
    from scrapers import citylights
    assert find_scraper("https://citylights.com/events/") is citylights
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/test_main.py -v -k "select_urls or find_scraper"`
Expected: FAIL — `ImportError: cannot import name 'find_scraper'`.

- [ ] **Step 3: Implement** — in `service/main.py` replace `_scrape_one` with:

```python
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
```

and in `run()` replace the block from `filters = source_filters or []` through the `urls = [...]` comprehension with:

```python
    urls = select_urls(load_sources(), source_filters, excludes)
```

- [ ] **Step 4: Run the whole main suite**

Run: `./.venv/bin/python -m pytest tests/test_main.py -v`
Expected: all pass (existing `run()` filter/ordering tests prove the refactor is behavior-preserving).

- [ ] **Step 5: Commit**

```bash
git add service/main.py service/tests/test_main.py
git commit -m "Extract find_scraper and select_urls from main.run"
```

---

### Task 3: `ci.py plan` and `ci.py scrape`

**Files:**
- Create: `service/ci.py`
- Test: `service/tests/test_ci.py`

**Interfaces:**
- Consumes: `main.select_urls`, `main.load_sources`, `main.find_scraper`, `RawEvent.to_dict`.
- Produces:
  - `ci.plan_matrix(source_filters=None, excludes=None) -> list[{"index": int, "url": str}]`
  - `ci.scrape_to_result(url: str) -> dict` with keys `url, status, source_name, error, events` (`events` = list of `RawEvent.to_dict()`).
  - `ci.cli(argv: list[str] | None = None) -> None` — subcommands `plan [--sources ...] [--exclude ...]` (prints compact JSON) and `scrape --url U --out PATH`.
  - `ci.py` imports `main` as `pipeline` and always calls through the module (`pipeline.find_scraper(...)`) so tests can monkeypatch `"main.<name>"`.

- [ ] **Step 1: Write the failing tests** — append to `service/tests/test_ci.py`:

```python
import ci


# --- plan / scrape ----------------------------------------------------------

def test_plan_matrix_indexes_filtered_sources(tmp_path, monkeypatch):
    f = tmp_path / "sources.txt"
    f.write_text("https://a.com\nhttps://b.com\nhttps://c.com\n")
    monkeypatch.setattr("main.SOURCES_FILE", f)
    assert ci.plan_matrix(["b.com", "c.com"]) == [
        {"index": 0, "url": "https://b.com"},
        {"index": 1, "url": "https://c.com"},
    ]
    assert [m["url"] for m in ci.plan_matrix()] == ["https://a.com", "https://b.com", "https://c.com"]


class _FakeScraper:
    NAME = "Fake Venue"

    def __init__(self, events=None, exc=None):
        self._events = events or []
        self._exc = exc

    def scrape(self, url):
        if self._exc:
            raise self._exc
        return self._events


def test_scrape_to_result_ok(monkeypatch):
    raw = _raw()
    monkeypatch.setattr("main.find_scraper", lambda url: _FakeScraper([raw]))
    r = ci.scrape_to_result("https://a.com")
    assert (r["status"], r["source_name"], r["error"]) == ("ok", "Fake Venue", None)
    assert [RawEvent.from_dict(e) for e in r["events"]] == [raw]


def test_scrape_to_result_records_exception(monkeypatch):
    monkeypatch.setattr("main.find_scraper",
                        lambda url: _FakeScraper(exc=RuntimeError("403 Forbidden")))
    r = ci.scrape_to_result("https://a.com")
    assert r["status"] == "error"
    assert r["error"] == "RuntimeError: 403 Forbidden"
    assert r["source_name"] == "Fake Venue"
    assert r["events"] == []


def test_scrape_to_result_no_scraper(monkeypatch):
    monkeypatch.setattr("main.find_scraper", lambda url: None)
    r = ci.scrape_to_result("https://x.com")
    assert (r["status"], r["source_name"]) == ("no_scraper", None)


def test_scrape_cli_writes_result_and_does_not_raise_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr("main.find_scraper", lambda url: _FakeScraper(exc=RuntimeError("boom")))
    out = tmp_path / "result.json"
    ci.cli(["scrape", "--url", "https://a.com", "--out", str(out)])  # must not raise
    assert json.loads(out.read_text())["status"] == "error"


def test_plan_cli_prints_compact_json(tmp_path, monkeypatch, capsys):
    f = tmp_path / "sources.txt"
    f.write_text("https://a.com\n")
    monkeypatch.setattr("main.SOURCES_FILE", f)
    ci.cli(["plan"])
    assert capsys.readouterr().out.strip() == '[{"index":0,"url":"https://a.com"}]'
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/test_ci.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ci'`.

- [ ] **Step 3: Implement** — create `service/ci.py`:

```python
"""CI stage entry points for the per-source GitHub Actions pipeline.

`.github/workflows/scrape.yml` fans out one runner per source and fans back in
to a single DB writer — see feature-specs/ci-scraping.md. Each subcommand is
one stage:

  plan   — print the filtered source list as a JSON matrix
  scrape — scrape ONE url into a result file (no DB, no creds; always exits 0)
  merge  — save every result into the DB in sources.txt order, classify, export
  guard  — sanity-check the new manifest vs main's and render the PR body

Everything goes through `pipeline.<fn>` (the main module) rather than
from-imports so tests can monkeypatch "main.<fn>".
"""
import argparse
import json
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
```

- [ ] **Step 4: Run to verify pass**

Run: `./.venv/bin/python -m pytest tests/test_ci.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add service/ci.py service/tests/test_ci.py
git commit -m "Add ci.py plan and scrape stages"
```

---

### Task 4: `ci.py merge`

**Files:**
- Modify: `service/ci.py`
- Test: `service/tests/test_ci.py`

**Interfaces:**
- Consumes: `main.init_db`, `main.load_sources`, `main.select_urls`, `main.save_events(raw_events, source) -> (saved, merged, skipped)`, `main.classify_upcoming(source_names=...)`, `main.export_json(path) -> int`, `main.DEFAULT_EVENTS_JSON`, `RawEvent.from_dict`.
- Produces:
  - `ci.load_results(results_dir: Path) -> dict[str, dict]` (URL → result; searches subdirectories).
  - `ci.merge_results(results_dir, source_filters=None, excludes=None, classify=True, events_json_path=None) -> dict` returning `{"sources": [row, ...], "exported": int, "failed": int}` where each row is `{"url", "name", "status", "events", "saved", "merged", "skipped", "error"}`.
  - CLI: `merge --dir DIR --report PATH [--sources ...] [--exclude ...] [--no-classify]` (writes report JSON).

- [ ] **Step 1: Write the failing tests** — append to `service/tests/test_ci.py`:

```python
# --- merge ------------------------------------------------------------------

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    with patch("main.get_session", return_value=session):
        yield session
    session.close()


@pytest.fixture
def pipeline_env(tmp_path, monkeypatch):
    sources = tmp_path / "sources.txt"
    sources.write_text("https://first.com\nhttps://second.com\nhttps://broken.com\nhttps://gone.com\n")
    monkeypatch.setattr("main.SOURCES_FILE", sources)
    monkeypatch.setattr("main.init_db", lambda: None)
    monkeypatch.setattr("main.export_json", lambda path: 0)
    return tmp_path


def _write_result(results_dir, artifact, url, status="ok", source_name=None, events=(), error=None):
    d = results_dir / artifact
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({
        "url": url, "status": status, "source_name": source_name, "error": error,
        "events": [e.to_dict() for e in events],
    }))


def test_merge_saves_in_sources_order_not_artifact_order(db_session, pipeline_env):
    shared = _raw(title="Shared Show", url=None)  # url None → dedup on title+start_time
    results = pipeline_env / "results"
    # second.com's artifact sorts first on disk; sources.txt order must still win.
    _write_result(results, "result-0", "https://second.com", source_name="Second", events=[shared])
    _write_result(results, "result-1", "https://first.com", source_name="First", events=[shared])

    report = ci.merge_results(results, source_filters=["first.com", "second.com"], classify=False)

    assert db_session.query(Event).one().sources == ["First", "Second"]
    by_url = {r["url"]: r for r in report["sources"]}
    assert by_url["https://first.com"]["saved"] == 1
    assert by_url["https://second.com"]["merged"] == 1
    assert report["failed"] == 0


def test_merge_failed_and_missing_sources_keep_existing_rows(db_session, pipeline_env):
    from main import save_events
    save_events([_raw(title="Old Show", url="https://broken.com/e/1")], source="Broken")
    results = pipeline_env / "results"
    _write_result(results, "result-2", "https://broken.com", status="error",
                  source_name="Broken", error="RuntimeError: 403")
    # gone.com has no artifact at all (job crashed / timed out).

    report = ci.merge_results(results, source_filters=["broken.com", "gone.com"], classify=False)

    assert db_session.query(Event).filter_by(title="Old Show").count() == 1
    by_url = {r["url"]: r for r in report["sources"]}
    assert by_url["https://broken.com"]["status"] == "error"
    assert by_url["https://gone.com"]["status"] == "missing"
    assert report["failed"] == 2


def test_merge_exports_to_given_path(db_session, pipeline_env, monkeypatch):
    seen = {}

    def fake_export(path):
        seen["path"] = path
        return 7

    monkeypatch.setattr("main.export_json", fake_export)
    out = pipeline_env / "out" / "events.json"
    report = ci.merge_results(pipeline_env / "results", classify=False, events_json_path=out)
    assert seen["path"] == out
    assert report["exported"] == 7


def test_merge_scopes_classification_on_filtered_run(db_session, pipeline_env, monkeypatch):
    seen = {}
    monkeypatch.setattr("main.classify_upcoming",
                        lambda source_names=None: seen.setdefault("scope", source_names))
    results = pipeline_env / "results"
    _write_result(results, "result-0", "https://first.com", source_name="First", events=[_raw()])
    ci.merge_results(results, source_filters=["first.com"])
    assert seen["scope"] == {"First"}


def test_merge_survives_classification_failure(db_session, pipeline_env, monkeypatch):
    def boom(source_names=None):
        raise RuntimeError("no AWS creds")

    monkeypatch.setattr("main.classify_upcoming", boom)
    report = ci.merge_results(pipeline_env / "results")  # must not raise
    assert report["exported"] == 0


def test_merge_cli_writes_report(db_session, pipeline_env):
    results = pipeline_env / "results"
    _write_result(results, "result-0", "https://first.com", source_name="First", events=[_raw()])
    report_path = pipeline_env / "report.json"
    ci.cli(["merge", "--dir", str(results), "--report", str(report_path),
            "--sources", "first.com", "--no-classify"])
    report = json.loads(report_path.read_text())
    assert report["sources"][0]["name"] == "First"
    assert report["sources"][0]["saved"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/test_ci.py -v -k merge`
Expected: FAIL — `AttributeError: module 'ci' has no attribute 'merge_results'`.

- [ ] **Step 3: Implement** — add `import os` to `ci.py`'s imports, then add these functions above `cli`:

```python
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
```

In `cli`, register the subcommand after the `scrape` parser:

```python
    m = sub.add_parser("merge", help="Save results to the DB in order, classify, export.")
    m.add_argument("--dir", type=Path, required=True)
    m.add_argument("--report", type=Path, required=True)
    m.add_argument("--sources", nargs="*", default=[], metavar="SUBSTRING")
    m.add_argument("--exclude", nargs="*", default=[], metavar="SUBSTRING")
    m.add_argument("--no-classify", action="store_true")
```

and dispatch it:

```python
    elif args.cmd == "merge":
        report = merge_results(args.dir, args.sources or None, args.exclude or None,
                               classify=not args.no_classify)
        args.report.write_text(json.dumps(report, indent=2))
```

- [ ] **Step 4: Run to verify pass**

Run: `./.venv/bin/python -m pytest tests/test_ci.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add service/ci.py service/tests/test_ci.py
git commit -m "Add ci.py merge stage: ordered single-writer save, classify, export"
```

---

### Task 5: `ci.py guard` and PR body

**Files:**
- Modify: `service/ci.py`
- Test: `service/tests/test_ci.py`

**Interfaces:**
- Consumes: the `merge` report dict from Task 4.
- Produces:
  - `ci.check_manifest(new_path, base_path, min_ratio=0.7) -> {"passed": bool, "changed": bool, "count": int, "base_count": int, "reason": str | None}`
  - `ci.render_pr_body(report: dict, guard: dict) -> str` (markdown; starts with `"Automated refresh"`).
  - CLI: `guard --new PATH --base PATH --report PATH --body PATH [--min-ratio 0.7]` — writes the body file and prints exactly three `key=value` lines (`passed=`, `changed=`, `count=`) for `$GITHUB_OUTPUT`. Always exits 0; the workflow acts on `passed`.

- [ ] **Step 1: Write the failing tests** — append to `service/tests/test_ci.py`:

```python
# --- guard ------------------------------------------------------------------

def _manifest(path, n, generated_at="2026-09-24T00:00:00+00:00"):
    path.write_text(json.dumps({"generated_at": generated_at, "taxonomy": {"version": 1},
                                "events": [{"id": i} for i in range(n)]}))
    return path


def test_guard_passes_at_threshold(tmp_path):
    g = ci.check_manifest(_manifest(tmp_path / "new.json", 70), _manifest(tmp_path / "base.json", 100))
    assert g["passed"] and g["reason"] is None


def test_guard_fails_below_threshold(tmp_path):
    g = ci.check_manifest(_manifest(tmp_path / "new.json", 69), _manifest(tmp_path / "base.json", 100))
    assert not g["passed"]
    assert "100 → 69" in g["reason"]


def test_guard_fails_on_invalid_manifest(tmp_path):
    (tmp_path / "new.json").write_text("{not json")
    g = ci.check_manifest(tmp_path / "new.json", _manifest(tmp_path / "base.json", 10))
    assert not g["passed"]


def test_guard_passes_when_main_has_no_manifest(tmp_path):
    g = ci.check_manifest(_manifest(tmp_path / "new.json", 5), tmp_path / "missing.json")
    assert g["passed"] and g["base_count"] == 0


def test_guard_ignores_generated_at_for_change_detection(tmp_path):
    g = ci.check_manifest(
        _manifest(tmp_path / "new.json", 3, generated_at="2026-09-25T00:00:00+00:00"),
        _manifest(tmp_path / "base.json", 3))
    assert g["changed"] is False


def test_pr_body_flags_failures_and_zero_event_sources():
    row = {"merged": 0, "skipped": 0}
    report = {"exported": 5, "failed": 1, "sources": [
        {**row, "url": "https://a.com", "name": "A", "status": "ok", "events": 5, "saved": 5, "error": None},
        {**row, "url": "https://b.com", "name": "B", "status": "ok", "events": 0, "saved": 0, "error": None},
        {**row, "url": "https://c.com", "name": "C", "status": "error", "events": 0, "saved": 0,
         "error": "RuntimeError: 403"},
    ]}
    guard = {"passed": True, "changed": True, "count": 5, "base_count": 5, "reason": None}
    body = ci.render_pr_body(report, guard)
    assert body.startswith("Automated refresh")
    assert "**Guard:** passed" in body
    assert "ok (0 events)" in body
    assert "RuntimeError: 403" in body


def test_guard_cli_emits_github_outputs(tmp_path, capsys):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"exported": 3, "failed": 0, "sources": []}))
    body = tmp_path / "body.md"
    ci.cli(["guard", "--new", str(_manifest(tmp_path / "new.json", 3)),
            "--base", str(_manifest(tmp_path / "base.json", 3)),
            "--report", str(report), "--body", str(body)])
    assert capsys.readouterr().out.splitlines() == ["passed=true", "changed=false", "count=3"]
    assert body.read_text().startswith("Automated refresh")
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/test_ci.py -v -k "guard or pr_body"`
Expected: FAIL — `AttributeError: module 'ci' has no attribute 'check_manifest'`.

- [ ] **Step 3: Implement** — add above `cli` in `ci.py`:

```python
def _read_events_manifest(path) -> tuple[dict, int]:
    manifest = json.loads(Path(path).read_text())
    return manifest, len(manifest["events"])


def check_manifest(new_path, base_path, min_ratio: float = 0.7) -> dict:
    """Guard the new manifest against main's before auto-merging it.

    Blocks when the new manifest is unreadable or its event count fell below
    `min_ratio` of main's. A missing/unreadable base (first run) passes.
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
        f"event count dropped {base_count} → {count} (below {min_ratio:.0%} of main)")
    return {"passed": passed, "changed": changed, "count": count,
            "base_count": base_count, "reason": reason}


_STATUS_ICON = {"ok": "✅", "no_scraper": "⚠️", "error": "❌", "missing": "❌", "save_error": "❌"}


def render_pr_body(report: dict, guard: dict) -> str:
    """Markdown PR body: guard verdict + per-source table, so the merged PR
    history doubles as a scrape log."""
    verdict = "passed" if guard["passed"] else f"FAILED — {guard['reason']}"
    lines = [
        "Automated refresh from the scheduled scrape workflow.",
        "",
        f"**Guard:** {verdict}",
        f"**Events:** {guard['base_count']} → {guard['count']} · "
        f"**Failed sources:** {report['failed']} of {len(report['sources'])}",
        "",
        "| Source | Status | Scraped | Saved | Merged | Skipped |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for r in report["sources"]:
        if r["status"] == "ok" and r["events"] == 0:
            status, icon = "ok (0 events)", "⚠️"
        else:
            status, icon = r["status"], _STATUS_ICON.get(r["status"], "❔")
        lines.append(f"| {r['name'] or r['url']} | {icon} {status} | {r['events']} | "
                     f"{r['saved']} | {r['merged']} | {r['skipped']} |")
    errors = [r for r in report["sources"] if r["error"]]
    if errors:
        lines += ["", "<details><summary>Errors</summary>", ""]
        lines += [f"- **{r['name'] or r['url']}**: `{r['error'].replace('`', "'")}`" for r in errors]
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"
```

In `cli`, register:

```python
    g = sub.add_parser("guard", help="Check the new manifest and render the PR body.")
    g.add_argument("--new", type=Path, required=True)
    g.add_argument("--base", type=Path, required=True)
    g.add_argument("--report", type=Path, required=True)
    g.add_argument("--body", type=Path, required=True)
    g.add_argument("--min-ratio", type=float, default=0.7)
```

and dispatch (stdout is consumed as `$GITHUB_OUTPUT`, so print nothing else):

```python
    elif args.cmd == "guard":
        guard = check_manifest(args.new, args.base, args.min_ratio)
        report = json.loads(args.report.read_text())
        args.body.write_text(render_pr_body(report, guard))
        print(f"passed={str(guard['passed']).lower()}")
        print(f"changed={str(guard['changed']).lower()}")
        print(f"count={guard['count']}")
```

- [ ] **Step 4: Run the full suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add service/ci.py service/tests/test_ci.py
git commit -m "Add ci.py guard stage: drop threshold, change detection, PR body"
```

---

### Task 6: Local end-to-end check (no commit)

Proves the stages compose against real Postgres and real scrapers before any workflow runs. Uses a throwaway Postgres on port 5433 so neither the Compose DB nor Neon is touched.

- [ ] **Step 1: Start a scratch Postgres**

```bash
docker run -d --rm --name agora-e2e -e POSTGRES_USER=agora -e POSTGRES_PASSWORD=agora \
  -e POSTGRES_DB=agora -p 5433:5432 postgres:16-alpine
```

- [ ] **Step 2: Scrape two sources into separate artifact dirs**

```bash
cd service
./.venv/bin/playwright install chromium
mkdir -p /tmp/agora-e2e/results/result-0 /tmp/agora-e2e/results/result-1
./.venv/bin/python ci.py scrape --url https://citylights.com/events/ --out /tmp/agora-e2e/results/result-0/result.json
./.venv/bin/python ci.py scrape --url https://www.commonwealthclub.org/events --out /tmp/agora-e2e/results/result-1/result.json
```
Expected: each prints `[<Name>] ok: <N> events` with N > 0.

- [ ] **Step 3: Merge and guard**

```bash
DATABASE_URL=postgresql://agora:agora@localhost:5433/agora EVENTS_JSON_PATH=/tmp/agora-e2e/events.json \
  ./.venv/bin/python ci.py merge --dir /tmp/agora-e2e/results --report /tmp/agora-e2e/report.json \
  --sources citylights.com commonwealthclub.org --no-classify
./.venv/bin/python ci.py guard --new /tmp/agora-e2e/events.json --base ../frontend/events.json \
  --report /tmp/agora-e2e/report.json --body /tmp/agora-e2e/body.md
cat /tmp/agora-e2e/body.md
```
Expected: merge prints `saved` counts matching the scraped counts; export count > 0. Guard prints `passed=false` — correct, because a two-source DB exports far fewer events than `main`'s full manifest. (This is also why the first CI run must be a **full** run — Task 8.) The body renders a two-row table.

- [ ] **Step 4: Re-run merge; confirm idempotence**

Re-run the Step 3 merge command. Expected: every source reports `0 saved … N skipped` (same-source re-scrape).

- [ ] **Step 5: Clean up**

```bash
docker stop agora-e2e && rm -rf /tmp/agora-e2e
```

---

### Task 7: The workflow

**Files:**
- Create: `.github/workflows/scrape.yml`

**Interfaces:**
- Consumes: `ci.py plan|scrape|merge|guard` CLIs (Tasks 3–5) and their outputs (`passed`/`changed`/`count`).

- [ ] **Step 1: Create `.github/workflows/scrape.yml`**

```yaml
name: Scrape and refresh manifest

# One runner per source (speed, fresh IP, fault isolation), fanned back in to a
# single DB writer. Design: feature-specs/ci-scraping.md.
on:
  schedule:
    - cron: "0 10 * * *" # ~3am PT; GitHub may start scheduled runs late
  workflow_dispatch:
    inputs:
      sources:
        description: "Only scrape sources whose URL contains one of these space-separated substrings (blank = all)"
        required: false
        default: ""

# One run at a time: runs share the Neon DB and events.json.
concurrency:
  group: scrape
  cancel-in-progress: false

permissions:
  contents: read

jobs:
  plan:
    runs-on: ubuntu-latest
    outputs:
      matrix: ${{ steps.plan.outputs.matrix }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
          cache: pip
          cache-dependency-path: service/requirements.txt
      - run: pip install -r service/requirements.txt
      - id: plan
        working-directory: service
        env:
          SOURCES: ${{ inputs.sources }}
        run: |
          set -f  # user input: split on spaces, but never glob
          echo "matrix=$(python ci.py plan --sources $SOURCES)" >> "$GITHUB_OUTPUT"

  scrape:
    needs: plan
    if: needs.plan.outputs.matrix != '[]'
    name: scrape (${{ matrix.source.url }})
    runs-on: ubuntu-latest
    timeout-minutes: 15
    strategy:
      fail-fast: false
      matrix:
        source: ${{ fromJSON(needs.plan.outputs.matrix) }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
          cache: pip
          cache-dependency-path: service/requirements.txt
      - run: pip install -r service/requirements.txt
      - name: Cache Playwright browsers
        id: pw-cache
        uses: actions/cache@v4
        with:
          path: ~/.cache/ms-playwright
          key: playwright-${{ runner.os }}-${{ hashFiles('service/requirements.txt') }}
      - if: steps.pw-cache.outputs.cache-hit != 'true'
        run: playwright install --with-deps chromium
      - if: steps.pw-cache.outputs.cache-hit == 'true'
        run: playwright install-deps chromium
      - name: Scrape
        working-directory: service
        env:
          URL: ${{ matrix.source.url }}
          ZYTE_API_KEY: ${{ secrets.ZYTE_API_KEY }}
        run: python ci.py scrape --url "$URL" --out result.json
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: result-${{ matrix.source.index }}
          path: service/result.json
          retention-days: 7
          if-no-files-found: ignore

  merge:
    needs: [plan, scrape]
    # Run even when scrape jobs failed — missing results keep their DB rows.
    if: always() && needs.plan.result == 'success' && needs.plan.outputs.matrix != '[]'
    runs-on: ubuntu-latest
    timeout-minutes: 30
    permissions:
      contents: write
      pull-requests: write
      actions: write   # gh workflow run deploy-pages.yml
      id-token: write  # AWS OIDC
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
          cache: pip
          cache-dependency-path: service/requirements.txt
      - run: pip install -r service/requirements.txt
      - uses: actions/download-artifact@v4
        with:
          pattern: result-*
          path: results
      - name: AWS credentials for Bedrock
        # Missing/broken creds only skip tagging; the manifest still ships.
        continue-on-error: true
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
          aws-region: ${{ secrets.AWS_REGION }}
      - name: Merge into DB, classify, export
        working-directory: service
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          EVENTS_JSON_PATH: ${{ github.workspace }}/frontend/events.json
          SOURCES: ${{ inputs.sources }}
        run: |
          set -f
          python ci.py merge --dir "$GITHUB_WORKSPACE/results" \
            --report "$RUNNER_TEMP/report.json" --sources $SOURCES
      - name: Guard
        id: guard
        working-directory: service
        run: |
          git show HEAD:frontend/events.json > "$RUNNER_TEMP/base.json" || true
          python ci.py guard --new "$GITHUB_WORKSPACE/frontend/events.json" \
            --base "$RUNNER_TEMP/base.json" --report "$RUNNER_TEMP/report.json" \
            --body "$RUNNER_TEMP/pr_body.md" >> "$GITHUB_OUTPUT"
      - name: Ship (PR → merge → deploy)
        env:
          GH_TOKEN: ${{ github.token }}
          PASSED: ${{ steps.guard.outputs.passed }}
          CHANGED: ${{ steps.guard.outputs.changed }}
          COUNT: ${{ steps.guard.outputs.count }}
        run: |
          set -euo pipefail
          if [ "$CHANGED" != "true" ] && git diff --quiet -- service/data/classifications.json; then
            echo "No data change; nothing to ship."
            exit 0
          fi
          branch="data/refresh-${GITHUB_RUN_ID}"
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git switch -c "$branch"
          git add frontend/events.json service/data/classifications.json
          git commit -m "Refresh manifest: ${COUNT} events ($(date -u +%F))"
          git push -u origin "$branch"
          gh pr create --base main --head "$branch" \
            --title "Refresh manifest: ${COUNT} events ($(date -u +%F))" \
            --body-file "$RUNNER_TEMP/pr_body.md"
          if [ "$PASSED" = "true" ]; then
            gh pr merge "$branch" --squash --delete-branch
            # Merges made with GITHUB_TOKEN don't trigger push workflows;
            # workflow_dispatch is the exception.
            gh workflow run deploy-pages.yml --ref main
          else
            gh pr comment "$branch" --body "Guard failed — not auto-merged. Review the report above."
            exit 1
          fi
```

- [ ] **Step 2: Lint the workflow**

Run: `docker run --rm -v "$PWD:/repo" --workdir /repo rhysd/actionlint:latest -color`
Expected: no errors. (Fix any reported issue; `shellcheck` warnings about the intentionally unquoted `$SOURCES` are acceptable because of `set -f`.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/scrape.yml
git commit -m "Add scheduled per-source scrape workflow with guarded auto-merge"
```

---

### Task 8: Docs

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `DEVELOPMENT.md`, `feature-specs/ci-scraping.md`

- [ ] **Step 1: `README.md`** — add a section `## Scheduled scraping (GitHub Actions)` with: what the workflow does (one line + link to the spec), how to trigger manually (`gh workflow run scrape.yml -f sources="citylights.com"`), and the one-time setup checklist copied from spec §6 (Neon project → AWS OIDC provider + role → the four secrets → *Allow GitHub Actions to create and approve pull requests* → first run must be a **full** run so the drop guard has a populated DB).

- [ ] **Step 2: `CLAUDE.md`** — in "Operating rules for agents", amend the "DB is ephemeral" bullet: the *local* DB is ephemeral; the CI DB (Neon) is persistent working state; the manifest is still what ships. Add a bullet: scheduled data refreshes arrive as bot PRs `data/refresh-*` that auto-merge — agents shouldn't regenerate/commit `events.json` for data refreshes anymore; a local run is for testing a scraper. In "Sharp edges" add: when a scraper's output changes, stale rows now also live in Neon — see spec §7 for the purge SQL. Add `ci.py` to the Layout list.

- [ ] **Step 3: `DEVELOPMENT.md`** — under "Design decisions" add **Per-source CI runners, single writer**: why fan-out (speed, IP, isolation), why one ordered writer (dedup attribution + the check-then-insert race on `(title, start_time)`), why an in-job guard instead of native auto-merge (`GITHUB_TOKEN` PRs don't trigger checks or push workflows).

- [ ] **Step 4: Correct the spec** — in `feature-specs/ci-scraping.md` "Design principles", the first bullet says no unique constraint enforces dedup. Replace `(no unique constraint enforces it)` with `(a partial unique index covers (url, start_time), but nothing enforces (title, start_time), and a unique violation would abort a source's save anyway)`.

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md DEVELOPMENT.md feature-specs/ci-scraping.md
git commit -m "Document scheduled CI scraping and its one-time setup"
```

---

### Task 9: One-time setup, PR, first runs

Mixed human/agent. `workflow_dispatch` only works once the workflow is on `main`, so the PR merges before the first run.

- [ ] **Step 1 (human): AWS** — in the external account: create the OIDC provider `token.actions.githubusercontent.com` (audience `sts.amazonaws.com`); create role with trust condition `token.actions.githubusercontent.com:sub = repo:TheAdityaKedia/agora:ref:refs/heads/main`; attach an inline policy allowing `bedrock:InvokeModel` on the Haiku 4.5 inference profile + foundation model used in `service/classify.py`; enable model access in the region.

- [ ] **Step 2 (agent, with the user's OK): secrets + repo setting**

```bash
cd /Users/kediaadi/workspace/LearningProjects/Agora
grep '^DATABASE_URL=' .env | cut -d= -f2- | tr -d '"' | gh secret set DATABASE_URL
gh secret set AWS_ROLE_ARN   # paste value from Step 1
gh secret set AWS_REGION --body us-east-1
grep '^ZYTE_API_KEY=' .env | cut -d= -f2- | gh secret set ZYTE_API_KEY   # if present
gh api -X PUT repos/TheAdityaKedia/agora/actions/permissions/workflow \
  -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true
gh secret list
```
Expected: four secrets listed. Confirm the Neon URL contains `sslmode=require`.

- [ ] **Step 3 (agent): push branch, open PR**

```bash
cd ../Agora-ci/service && ./.venv/bin/python -m pytest -q && cd ..
git push -u origin ci-scraping
gh pr create --base main --head ci-scraping --title "Scrape every source on its own GitHub runner" \
  --body "Implements feature-specs/ci-scraping.md. See the plan for task breakdown."
```

- [ ] **Step 4 (human): review + merge the PR.**

- [ ] **Step 5 (agent): first run — FULL** (populates Neon; a filtered first run would trip the drop guard against `main`'s full manifest)

```bash
gh workflow run scrape.yml
gh run watch "$(gh run list --workflow scrape.yml --limit 1 --json databaseId -q '.[0].databaseId')"
```
Expected: ~48 scrape jobs; merge job green; a `data/refresh-*` PR created and squash-merged; a `Deploy frontend to Pages` run dispatched. Inspect the PR body's failure list and compare the event count to `main`'s pre-run count.

- [ ] **Step 6 (agent): filtered run**

```bash
gh workflow run scrape.yml -f sources="citylights.com"
```
Expected: one scrape job; guard passes (Neon still holds every source); either "No data change" or a small auto-merged PR.

- [ ] **Step 7: Clean up the worktree**

```bash
cd /Users/kediaadi/workspace/LearningProjects/Agora && git worktree remove ../Agora-ci
```
