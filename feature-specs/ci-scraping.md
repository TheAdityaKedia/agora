# Feature Spec: Scheduled Scraping on GitHub Actions (one runner per source)

Status: Implemented (branch `ci-scraping`) · Owner: Agora · Target: `.github/workflows/scrape.yml` + `service/ci.py`

## Goal

Run the full pipeline (scrape → save → classify → export → deploy) on
GitHub-hosted runners on a schedule, with **every source scraped on its own
runner**. That buys three things at once:

1. **Speed** — ~48 sources scrape concurrently (20 at a time on the Free plan)
   instead of 6 threads on one machine.
2. **Fresh IP per source** — no single runner IP accumulates Cloudflare/WAF
   reputation from hitting every venue (see `DEVELOPMENT.md` → "IP reputation").
3. **Fault isolation** — a hung, crashing, or blocked scraper can't slow or
   break any other source.

The repo is public, so standard runners are free with unlimited minutes.

**Non-goals for v1:**
- Pruning past events from the DB (volume is tiny; export already hides them).
- A dispatch input to purge + re-scrape one source (manual SQL for now, §7).
- Batching DB writes (only if merge proves slow).
- Replacing the local Docker Compose workflow — it keeps working unchanged.

## Design principles

- **Fan-out scrape, single-writer save.** Only scraping is parallel. Exactly one
  job writes to the DB, in `sources.txt` order. Parallel writers would make
  dedup attribution nondeterministic (the earlier source wins a shared row) and
  race on the check-then-insert dedup (a partial unique index covers
  `(url, start_time)`, but nothing enforces `(title, start_time)`, and a unique
  violation would abort a source's save).
- **Scrape jobs are unprivileged.** They get no DB, AWS, or git credentials —
  only `ZYTE_API_KEY`. All privileged work happens in the one merge job.
- **Failures are data, not red jobs.** A scraper exception is recorded in its
  result file and the job exits 0; the merge job decides what it means.
- **A failed source keeps its last rows.** The hosted DB persists between runs,
  so a source that fails simply writes nothing this run — exactly today's local
  behavior. No rehydration logic.
- **Reuse `main.py`.** `_scrape_one`, `save_events`, `classify_upcoming`, and
  `export_json` are called as-is; the new code is orchestration + serialization.

## 1. Workflow shape — `.github/workflows/scrape.yml`

**Triggers**
- `schedule`: daily, ~3am PT (`0 10 * * *` UTC).
- `workflow_dispatch` with optional input `sources` — space-separated substring
  filter, same semantics as `main.py --sources` (plain substring; mind
  collisions like `sfpl` vs `sfplayhouse`).

**Concurrency:** `group: scrape`, `cancel-in-progress: false` — one run at a
time (the "one scrape run at a time" rule in `CLAUDE.md`).

**Jobs**

| Job | Runs | Does |
|-----|------|------|
| `plan` | 1 | Reads `service/data/sources.txt`, applies the `sources` filter, emits the URL list as a JSON output. |
| `scrape` | 1 per URL | `strategy.matrix.url: ${{ fromJSON(needs.plan.outputs.urls) }}`, `fail-fast: false`, `timeout-minutes: 15`. Runs `ci.py scrape`, uploads `result.json` as artifact `result-<index>`. |
| `merge` | 1 | `needs: [plan, scrape]`, `if: always()` (runs even when scrape jobs fail). Downloads all `result-*` artifacts, runs `ci.py merge`, guards, ships (§4). |

**Scrape job setup:** native (not the Docker image — building it 48× is slower):
Python 3.13 via `actions/setup-python` with pip cache, `pip install -r
requirements.txt`, and `playwright install --with-deps chromium` with
`~/.cache/ms-playwright` cached (`actions/cache`, keyed on the Playwright
version). The matrix key is the URL; the artifact name uses a sanitized form
or the matrix index to stay valid.

## 2. New code — `service/ci.py`

Two subcommands; both import from `main.py`.

### `python ci.py scrape --url URL --out result.json`

Calls `_scrape_one(url)` and **always** writes a result file, then exits 0:

```json
{
  "url": "https://citylights.com/events/",
  "status": "ok",            // ok | error | no_scraper
  "source_name": "City Lights",
  "error": null,             // "TypeName: message" when status == error
  "events": [ { "title": "...", "start_time": "2026-10-01T02:00:00+00:00",
                "location": "...", "url": "...", "description": "...",
                "image_url": null } ]
}
```

A job that crashes before writing, or hits `timeout-minutes`, produces **no
artifact** — the merge job treats a missing result as `status: missing`
(a failure).

### `python ci.py merge --dir results/ [--sources ...]`

1. `init_db()` against `DATABASE_URL` (Neon; creates schema on first run).
2. Load all result files, index by URL.
3. Walk the (filtered) `sources.txt` URLs **in file order**. For each `ok`
   result, deserialize events and call `save_events(events, source=name)`.
   `error` / `missing` / `no_scraper` → log, save nothing (rows kept).
4. `classify_upcoming(source_names=scope)` — `scope` is the set of sources
   scraped this run when a `sources` filter was given, else `None` (full). A
   classify failure is logged and does not abort (same guard as `run()`).
5. `export_json(EVENTS_JSON_PATH)`.
6. Write `report.json`: per-source `{name, url, status, saved, merged, skipped,
   error}`, plus totals and the exported event count.

`save_events` currently returns counts but `_save_scraped` only prints them;
`merge` calls `save_events` directly to capture them.

### Supporting change — `scrapers/base.py`

Add `RawEvent.to_dict()` / `RawEvent.from_dict()` (ISO-8601 `start_time`,
tz-aware; `None` fields preserved). No other scraper changes.

## 3. Hosted database — Neon

- Neon free-tier Postgres project, region **AWS us-east** (closest to GitHub's
  runners; `save_events` does ~2 round-trips per event, so latency matters).
- Connection string with `sslmode=require` stored as secret `DATABASE_URL`.
  Only the merge job receives it.
- Data size is a few MB (~4k events); free-tier storage and compute are ample
  for one daily run. Neon suspends when idle and wakes on connect.
- **Consequence:** the CI DB is persistent, so `CLAUDE.md`'s "the DB is
  ephemeral" statement no longer holds for CI. The manifest is still what ships
  and remains the durable artifact; the DB is the durable *working state*.

## 4. Shipping — guarded auto-merge PR

Runs in the merge job after `ci.py merge`.

**Guards** (all must pass):
- `frontend/events.json` parses as JSON.
- Event count ≥ **70%** of the count in `main`'s current `events.json` (a >30%
  drop blocks).

Failed / zero-event sources are listed in the report but **do not block** —
their rows are retained, so they can't cause a drop by themselves.

**No change → no PR.** If `events.json` and `classifications.json` are
unchanged, exit.

**On pass:**
1. Branch `data/refresh-<run_id>`; one data-only commit containing
   `frontend/events.json` + `service/data/classifications.json`
   (commit subject: `Refresh manifest: <N> events (<run date>)`).
2. `gh pr create` with body = report table (per-source status + counts,
   failures highlighted).
3. `gh pr merge --squash --delete-branch` immediately.
4. `gh workflow run deploy-pages.yml` — required because pushes/merges made
   with `GITHUB_TOKEN` don't trigger other workflows; `workflow_dispatch` is
   the exception.

**On fail:** open the PR anyway, leave it **unmerged**, and comment which guard
tripped. Nothing deploys; a human reviews.

Why not native `gh pr merge --auto`: it only waits on required status checks,
and checks don't run on PRs opened by `GITHUB_TOKEN`, so it would stall without
a GitHub App / PAT. Guarding in-job avoids both branch protection and extra
tokens.

**Workflow permissions:** `contents: write`, `pull-requests: write`,
`actions: write` (dispatch deploy), `id-token: write` (AWS OIDC).
Repo setting required: *Settings → Actions → General → Allow GitHub Actions to
create and approve pull requests*.

## 5. AI tagging credentials — external AWS account via OIDC

- A dedicated (non-Isengard) AWS account for Bedrock.
- IAM OIDC identity provider for `token.actions.githubusercontent.com`.
- Role trusted only for `repo:TheAdityaKedia/agora:ref:refs/heads/main`
  (scheduled and dispatch runs on `main`), policy limited to
  `bedrock:InvokeModel` on the Claude Haiku model used by `classify.py`
  (plus model access enabled in the region).
- Merge job: `aws-actions/configure-aws-credentials` with
  `role-to-assume: ${{ secrets.AWS_ROLE_ARN }}`, `aws-region: ${{ secrets.AWS_REGION }}`.
  No long-lived keys stored.
- Missing/failed creds → classify is skipped (existing guard); the manifest
  still ships, new shows untagged.

## 6. Secrets & one-time setup checklist

| Secret | Used by | Purpose |
|--------|---------|---------|
| `DATABASE_URL` | merge | Neon connection string (`sslmode=require`) |
| `AWS_ROLE_ARN` | merge | OIDC role for Bedrock |
| `AWS_REGION` | merge | Bedrock region |
| `ZYTE_API_KEY` | scrape | Metered fetch for Green Apple (optional; absent → that scraper returns 0) |

Setup (documented in `README.md`): create Neon project → create AWS OIDC
provider + role → add secrets → enable "Allow GitHub Actions to create and
approve pull requests" → first manual dispatch.

## 7. Operations

- **Scraper output changed** (URLs/descriptions/times, not just new events):
  saves never update existing rows, so delete that source's rows in the Neon
  SQL console before the next run, e.g.
  `DELETE FROM events WHERE sources->>0 = '<NAME>';` (verify against the
  schema first). A purge dispatch input is deferred.
- **Full reset:** drop the tables in Neon; the next run re-creates and fully
  re-populates.
- **Known platform limits:** GitHub cron can run 5–30 min late; scheduled
  workflows on public repos are auto-disabled after 60 days without repo
  activity (daily bot merges should count).

## 8. Testing

**Unit (pytest, no network):**
- `RawEvent` dict round-trip — tz-aware datetimes, `None` fields.
- `ci.py scrape` writes `status: error` (and exits 0) when the scraper raises;
  `no_scraper` for an unmatched URL.
- `ci.py merge` saves in `sources.txt` order regardless of result-file order:
  two sources sharing an event → the earlier source owns the row, the later
  merges.
- `error` / missing results leave existing rows untouched.
- Drop guard: passes at 70%, fails below.

**Local end-to-end:** `ci.py scrape` for 2–3 sources into a temp dir, then
`ci.py merge` against the Compose Postgres; diff the manifest vs a normal
`main.py` run for those sources.

**Workflow:** manual dispatch with `sources=citylights.com` against Neon →
verify PR opened, merged, and Pages deploy triggered. Then one full dispatch
before enabling reliance on the cron.

## Files touched

- `.github/workflows/scrape.yml` (new)
- `service/ci.py` (new) + `service/tests/test_ci.py` (new)
- `service/scrapers/base.py` (`RawEvent` serialization)
- `README.md` (setup checklist), `CLAUDE.md` (CI DB is persistent; how data
  refreshes ship), `DEVELOPMENT.md` (design decision entry)
