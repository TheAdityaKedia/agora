# Agora

Agora is an event aggregator that collects happenings from around your city into a single calendar.

It monitors a curated list of event sources — bookstore websites, dance venues, community organizations — and keeps a unified, searchable calendar up to date. Events are automatically tagged by type, making it easy to filter by what you're in the mood for: a bachata social, an author talk, a free poetry reading.

You can also submit events directly by forwarding an email or sending a screenshot of a flyer. Agora will parse it and add it to the calendar.

**Live site:** https://theadityakedia.github.io/agora/

## What it does

- Scrapes event listings from configured websites on a schedule
- **Auto-tags every event by AI** on two axes — **type** (the format: performance, screening, talk, workshop, exhibition, social) and **topic** (the interest: poetry, jazz, theater, film, wellness, LGBTQ+, …) — plus a free/paid cost, classified once per show and cached
- Serves a static calendar UI backed by a JSON manifest, with **type/topic filters** (click a chip, pick from the controls, or just search a tag word), typo-tolerant client-side search, and shareable URL-encoded views
- Exposes a REST API for querying events by date range or source
- Monitors a Gmail inbox for forwarded emails and flyer images *(planned)*

## Repo layout

```
agora/
├── service/                    # Python backend
│   ├── scrapers/               # One module per source; performances.py = shared
│   │                           #   run → per-performance expansion helper
│   ├── exporters/              # DB → JSON manifest (joins in tags)
│   ├── taxonomy.py             # Two-axis tag taxonomy loader (type + topic)
│   ├── classify.py             # AI classifier (Bedrock/Claude Haiku) + prompt
│   ├── classifications.py      # Committed per-show tag cache
│   ├── data/                   # sources.txt, taxonomy.v1.json,
│   │                           #   source_profiles.json, classifications.json
│   ├── tests/                  # Pytest suite
│   ├── main.py                 # Scrape (concurrent) → save → classify → export
│   ├── api.py                  # FastAPI REST API for the DB
│   ├── models.py, db.py        # SQLAlchemy (Postgres) schema + session
│   ├── config.py               # Shared config (look-ahead horizon, etc.)
│   ├── Dockerfile              # Python + Playwright + Chromium image
│   └── requirements.txt
├── frontend/                   # Static site served by GitHub Pages
│   ├── index.html              # Self-contained page (inline CSS/JS, no build)
│   ├── vendor/                 # Vendored client libs (MiniSearch, pinned)
│   └── events.json             # Manifest the scraper writes, the page reads
├── .github/workflows/          # CI
│   └── deploy-pages.yml        # Publishes frontend/ to GitHub Pages
├── docker-compose.yml          # Postgres + API + scraper for local runs
├── CONTRIBUTING.md             # How to add a source (scraper) + write feature specs
├── feature-specs/              # Design specs for larger features (e.g. tagging)
├── DEVELOPMENT.md              # Phasing, design decisions, current status
└── README.md
```

## How the pipeline works

A small, decoupled pipeline:

1. **Scrape** — `service/main.py` reads sources from `service/data/sources.txt` and dispatches to per-source scraper modules, run **concurrently** in a thread pool (`--workers` / `SCRAPER_WORKERS`, default 6). Static sources use plain HTTP; JS-rendered or WAF-protected ones (Cloudflare, Ticketweb, …) drive a headless Chromium via Playwright. Season-show/ticketing sources expand a run into **one event per performance** (shared `scrapers/performances.py`), and many fetch each event's detail page for a real description.
2. **Store** — Events land in Postgres. Dedup keys on **(URL + start_time)**, then **(title + start_time)** — `start_time` is always part of the key, so a show that plays many nights is kept as one row per performance (many performances can share one show URL). Cross-source duplicates merge their `sources`. Events beyond a 12-month look-ahead are dropped. The DB is an ephemeral working store; saves **skip** existing rows (they don't update them).
3. **Classify** — New shows (distinct `title`+`source`) are tagged by an LLM (Amazon Bedrock, Claude Haiku) into the two-axis taxonomy + cost, using the venue's `source_profiles.json` line as a prior. Results are cached in the committed `service/data/classifications.json` — "classify once, cache forever," so a steady-state run makes zero LLM calls. Skipped gracefully if AWS creds aren't present.
4. **Export** — A JSON manifest is written to `frontend/events.json`, filtered to upcoming events, joining each event to its show's tags and shipping the taxonomy inline. This is the artifact the site reads.
5. **Serve** — The static frontend fetches `./events.json` at load and renders events grouped by day, with source/date/type/topic filters, tag search, and shareable URL state.

## Running locally

Requires a Docker runtime (Docker Desktop, or Colima: `brew install colima docker docker-compose && colima start`).

```bash
docker compose up -d db api          # Postgres + API on http://localhost:8000 (docs at /docs)
docker compose run --rm scraper      # scrape once → save to Postgres → write frontend/events.json

# Preview the frontend against the fresh manifest
python3 -m http.server 8080 -d frontend
open http://127.0.0.1:8080
```

Stop with `docker compose down` (add `-v` to also wipe the database volume).

**AI tagging** runs as part of the scrape (step 3 above). It calls Amazon
Bedrock, so the scraper container needs AWS creds — `docker-compose.yml` mounts
`~/.aws` and sets `AWS_REGION`; refresh your creds on the host first. Without
valid creds the classify step is **skipped** (events export untagged) — or pass
`--no-classify` to skip it explicitly. Only new/uncached shows cost anything
(fractions of a cent); a steady-state run makes zero calls.

## Updating the site

The live site auto-redeploys on any push to `main` that touches `frontend/` (or the deploy workflow itself):

- **Editing the UI** — change `frontend/index.html`, commit, push. Pages redeploys in ~1 minute.
- **Refreshing event data** — happens automatically every day via GitHub Actions (see [Scheduled scraping](#scheduled-scraping-github-actions) below). A local `docker compose run --rm scraper` still regenerates `frontend/events.json` against the local DB — use it to test a scraper, not to ship data.
  > ⚠️ **When a scraper's *output* changes** (URLs, descriptions, times — not just new events), first wipe the DB with `docker compose down -v` and bring it back up, then re-scrape. Saves **skip** rows that already exist and never update them, so stale fields otherwise linger in the manifest.
- **Running a subset** — filter which sources run (substring match on the URL):
  ```bash
  docker compose run --rm scraper python main.py --sources gamh.com ybca.org
  docker compose run --rm scraper python main.py --exclude sfjazz sfpl   # skip slow/rate-limited ones
  docker compose run --rm scraper python main.py --workers 6             # concurrency (or SCRAPER_WORKERS)
  ```
  The manifest is always rebuilt from the full DB, so a subset run adds to the site without dropping events from sources that weren't scraped this time.
- **Adding a source** — see `CONTRIBUTING.md` for the scraper-authoring playbook.
- **Manual redeploy** — Actions tab → *Deploy frontend to Pages* → *Run workflow*.

Deploy status: https://github.com/TheAdityaKedia/agora/actions

## Scheduled scraping (GitHub Actions)

`.github/workflows/scrape.yml` runs daily (~3am PT) and on demand. Every source
scrapes on **its own runner** (speed, a fresh IP per source, fault isolation);
one merge job then saves all results into Neon Postgres in `sources.txt` order,
classifies new shows, exports `events.json`, and ships it as a bot PR
(`data/refresh-<run_id>`) that auto-merges when the guard passes (manifest
parses; event count ≥ 70% of `main`'s), then triggers the Pages deploy. A
failed or timed-out source keeps its rows from earlier runs. Design:
`feature-specs/ci-scraping.md`; stage code: `service/ci.py`.

```bash
gh workflow run scrape.yml                              # full run
gh workflow run scrape.yml -f sources="citylights.com"  # one source
```

Runs from a branch other than `main` use the `DATABASE_URL_TEST` Neon branch,
ship into a throwaway `ci-sandbox/<run_id>` branch, and never deploy — safe for
testing workflow changes.

**One-time setup**

1. **Neon** — a project with a `production` branch (DB) and a `ci-test` child
   branch (test runs).
2. **AWS (Bedrock tagging)** — in the external AWS account: an IAM OIDC provider
   for `token.actions.githubusercontent.com` (audience `sts.amazonaws.com`), and
   a role trusted only for `repo:TheAdityaKedia/agora:ref:refs/heads/main`
   allowing `bedrock:InvokeModel` on the Haiku model in `service/classify.py`.
   Without it, runs still ship — new shows are just untagged.
3. **Repo secrets** — `DATABASE_URL` (Neon production, pooled, `sslmode=require`),
   `DATABASE_URL_TEST` (Neon `ci-test`), `AWS_ROLE_ARN`, `AWS_REGION`,
   `ZYTE_API_KEY` (optional; Green Apple).
4. **Repo setting** — Settings → Actions → General → *Allow GitHub Actions to
   create and approve pull requests*.
5. **First run on `main` must be a full run** (the drop guard compares against
   `main`'s manifest, so a filtered run against a near-empty DB would be blocked).

**Operations** — when a scraper's *output* changes (not just new events), its
stale rows live on in Neon (saves never update): delete them in the Neon SQL
editor before the next run, e.g. `DELETE FROM events WHERE sources->>0 = '<NAME>';`.
