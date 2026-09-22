# Agora

Agora is an event aggregator that collects happenings from around your city into a single calendar.

It monitors a curated list of event sources — bookstore websites, dance venues, community organizations — and keeps a unified, searchable calendar up to date. Events are automatically tagged by type, making it easy to filter by what you're in the mood for: a bachata social, an author talk, a free poetry reading.

You can also submit events directly by forwarding an email or sending a screenshot of a flyer. Agora will parse it and add it to the calendar.

**Live site:** https://theadityakedia.github.io/agora/

## What it does

- Scrapes event listings from configured websites on a schedule
- Monitors a Gmail inbox for forwarded emails and flyer images *(planned)*
- Extracts structured event data using AI *(planned)*
- Auto-tags events into a hierarchical type taxonomy + cost, so you can filter by kind *(planned — design in `feature-specs/tagging.md`)*
- Exposes a REST API for querying events by date range, tag, or source
- Serves a static calendar UI backed by a JSON manifest, with typo-tolerant client-side search over titles, descriptions, locations, and sources

## Repo layout

```
agora/
├── service/                    # Python backend
│   ├── scrapers/               # One module per source; performances.py = shared
│   │                           #   run → per-performance expansion helper
│   ├── exporters/              # DB → JSON manifest
│   ├── data/                   # sources.txt (the source URL list)
│   ├── tests/                  # Pytest suite
│   ├── main.py                 # Scrape (concurrent) → save → export entry point
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
3. **Export** — A JSON manifest is written to `frontend/events.json`, filtered to upcoming events. This is the artifact the site reads.
4. **Serve** — The static frontend fetches `./events.json` at load and renders events grouped by day.

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

## Updating the site

The live site auto-redeploys on any push to `main` that touches `frontend/` (or the deploy workflow itself):

- **Editing the UI** — change `frontend/index.html`, commit, push. Pages redeploys in ~1 minute.
- **Refreshing event data** — run `docker compose run --rm scraper` locally to regenerate `frontend/events.json`, then commit and push it.
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
