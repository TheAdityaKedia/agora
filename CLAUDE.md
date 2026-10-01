# CLAUDE.md — start here

Orientation for an agent working on Agora. This is a **router**, not a manual —
it points you at the right doc and lists the operating rules and sharp edges the
other docs don't cover. Read the linked doc for depth; don't duplicate it here.

## What Agora is (30 seconds)

An SF Bay Area event aggregator. Scrapers pull events from ~29 venue/organizer
sites into Postgres; an LLM tags each show (type + topic + cost); an exporter
writes `frontend/events.json`; a static single-page site on GitHub Pages reads
that JSON and renders a searchable, filterable (source/date/type/topic),
shareable calendar. No live backend serves the site — the committed
`events.json` is the artifact that ships.

Pipeline: `sources.txt → scrapers (concurrent) → Postgres → classify (cached) → events.json → Pages`

## Where to read for your task

| Task | Read |
|------|------|
| What's planned next (features + their specs) | `future-features.md` |
| Add or fix a **scraper** (the most common change) | `CONTRIBUTING.md` |
| Run it locally / deploy / update the site | `README.md` |
| Frontend search (MiniSearch, ranking) | code: `frontend/index.html` |
| AI tagging — taxonomy, classifier, cache, filters | code: `service/taxonomy.py`, `classify.py`, `classifications.py`; venue priors in `source_profiles.json` |
| Scheduled CI scraping, alerts, secrets, Neon | `README.md` → "Scheduled scraping"; code: `service/ci.py`, `.github/workflows/scrape.yml` |
| Event submissions by email (Gmail → events) | `README.md` → "Event submissions by email"; code: `service/ingest/`, `.github/workflows/ingest-email.yml` |
| Cross-source duplicate merging | code: `service/dedup.py` (save-time), `service/dedupe_existing.py` (one-off cleanup) |
| Candidate sources to onboard next | `future-sources.md` |
| Add a **new subsystem/feature** (not a scraper) | write a spec in `feature-specs/` first — see `CONTRIBUTING.md` |

## Layout

- `service/` — Python backend. `scrapers/` (one module per source + shared libs
  `eventbrite.py`, `luma.py`, `performances.py`, `browser.py`), `exporters/`,
  `taxonomy.py` + `classify.py` + `classifications.py` (AI tagging), `main.py`
  (pipeline entry), `ci.py` (GitHub Actions stages: plan/scrape/merge/guard),
  `dedup.py` (cross-source fuzzy dedup), `ingest/` (email submissions:
  mailbox, message, extract, validate, links, images, run), `api.py` (read
  API), `tests/`. `data/` holds `sources.txt`,
  `taxonomy.v1.json`, `source_profiles.json` (tagging venue priors), and the
  committed `classifications.json` (tag cache).
- `frontend/` — `index.html` (self-contained, inline CSS/JS, no build),
  `vendor/` (pinned MiniSearch), `events.json` (the manifest — carries the
  taxonomy block + per-event `types`/`topics`/`cost`).
- `.github/workflows/deploy-pages.yml` — deploys `frontend/` on push to `main`.
- `.github/workflows/scrape.yml` — daily + on-demand scrape: one runner per
  source → single merge job (Neon) → guarded auto-merged data PR → deploy.
  Stages in `service/ci.py`; setup + ops in `README.md` → "Scheduled scraping".
- `.github/workflows/ingest-email.yml` — hourly: Gmail inbox → events
  ("Community submissions") → same ship path. `.github/actions/ship-manifest/`
  is the guard → data PR → merge → deploy step both workflows share; both hold
  the `neon-writer-<ref>` concurrency lock.

## Operating rules for agents

- **Tests:** `cd service && ./.venv/bin/python -m pytest`. Add tests for every
  change; parsers are pure functions tested against trimmed real fixtures in
  `service/tests/fixtures/`. Run the suite before committing.
- **Verify before claiming.** Don't say something works until you've run it. You
  **cannot drive a browser interactively** — to verify frontend behavior, drive
  headless Chromium via the Playwright already in `service/.venv` (load the page
  from a local `python3 -m http.server`, capture `pageerror`/console, assert on
  rendered DOM). This is how the "stuck on Loading…" bug was caught.
- **Running scrapers:** `docker compose run --rm scraper python main.py`
  (optionally `--sources <substr>` / `--exclude <substr>` / `--workers N`).
  Rebuild the image (`docker compose build scraper`) after changing Python — the
  container won't pick up edits otherwise.
- **When a scraper's *output* changes** (URLs, descriptions, times — not just
  new events): wipe the DB (`docker compose down -v`, then `up -d db`) and
  re-scrape. Saves **skip** existing rows; they are never updated, so stale
  fields linger otherwise.
- **Commits:** one logical change per commit; keep the large regenerated
  `events.json` in its **own** commit so code stays reviewable. Imperative
  subject, no attribution footer (match `git log`). Commit/push only when asked;
  a push to `main` touching `frontend/` deploys the site.
- **Where to commit:** small requests (docs, notes, config tweaks, small fixes)
  go **straight to `main`** — no branch, no PR. Use a **branch + PR** only for
  big features that affect the **site** (`frontend/`, the manifest) or the
  **jobs** (`scrape.yml`, `ci.py`, the scrape/merge pipeline); test job changes
  from the branch (non-`main` runs use the `ci-test` DB and never deploy).
- **The manifest is durable; the DB is working state.** Never treat DB state as
  the source of truth for the site — `events.json` is. Same for tags:
  `classifications.json` is the durable committed cache, not the DB. The
  *local* Compose DB is ephemeral; the CI DB (Neon) persists between runs.
- **Data refreshes ship from CI, not from agents.** `scrape.yml` runs daily and
  lands `events.json` + `classifications.json` via auto-merged
  `data/refresh-*` PRs. Don't regenerate/commit the manifest to ship data; a
  local run is for testing a scraper. To refresh one source now:
  `gh workflow run scrape.yml -f sources="<substring>"`.
  Sources that 403 from GitHub runners live in
  `service/data/local_only_sources.txt`; `scripts/scrape-to-neon.sh --blocked`
  scrapes them locally into Neon. Add a source there when CI can't reach it.
  Failing/0-event sources (except those) open a `Scrape failures` issue and
  turn the run red — if you see one, that's the to-do list.
- **AI tagging (classify step in `run()`):** calls Bedrock (Claude Haiku); needs
  AWS creds (the compose scraper mounts `~/.aws` — refresh on host first) or it's
  skipped (`--no-classify` to skip explicitly). Classifies only cache misses
  (new shows / stale taxonomy version), keyed per `(source, title)`. Adding a
  source ⇒ also add its `source_profiles.json` line (see `CONTRIBUTING.md`).

## Sharp edges (things that have actually bitten)

- **Email submissions publish automatically and replies go to strangers.**
  Keep sender data (address, subject, body, images) out of logs, PRs, the DB
  and the manifest; publish an image only if it passes the safety check in
  `ingest/images.py` (never screenshots). Test ingest changes from a branch —
  non-`main` runs use the `ci-test` DB but **share the real inbox**, so they
  label and reply to real mail.
- **Stale rows live in Neon too.** Saves never update existing rows, so when a
  scraper's *output* changes, CI keeps serving the old fields until that
  source's rows are deleted in Neon (README → "Scheduled scraping" →
  Operations). Hosted Postgres also drops idle connections — `db.py` uses
  `pool_pre_ping` for that; keep it.

- **`--exclude`/`--sources` are plain substring matches.** `sfpl` also matches
  `sfplayhouse`; use `sfpl.org`. Check for collisions before trusting a filter.
- **Cloudflare/WAF sources: work the ladder in `CONTRIBUTING.md` → "When to
  give up"** before declaring a source dead. Two looked hopeless and are solved:
  **SFJAZZ** via an un-fronted origin API (`sfjazz.py`), and **Green Apple** via
  full Chromium (`browser_context(full_chromium=True)`) + its robots.txt
  crawl-delay + a fresh context on challenge. Both are free. Gotcha: a scraper
  can **pass on your Mac and fail in Docker** (Playwright's default headless
  shell gets caught by Cloudflare in the Linux container), so always verify in
  Docker. Great Star's TicketTailor pages now pass the same way (fresh-context
  retry). Still blocked, and deliberately left alone: GAMH's Eventim event pages
  (Cloudflare Turnstile CAPTCHA; we don't get past CAPTCHAs) and Fillmore's
  Ticketmaster pages (401; the sanctioned route would be Ticketmaster's
  Discovery API, which needs a key). Fingerprint/stealth patches were tried and
  *reverted* (they broke rendering). Don't go there. Also: GAMH's calendar is
  paginated behind "Load more" (`gamh.py`), and the owner OK'd using that
  endpoint despite robots.txt `Disallow: /*?`.
  `scrapers/zyte.py` (paid, `ZYTE_API_KEY` in `.env`) is the unused last resort.
- **Frontend JS runs one big IIFE** — mind temporal-dead-zone ordering. A `const`
  referenced during page-load init (e.g. from `readStateFromUrl`) must be
  declared before that code runs, or a shared `?q=` link hangs on "Loading…".
- **Re-exporting doesn't need a scrape:**
  `docker compose run --rm scraper python -m exporters.json_export --out /out/events.json`
  rebuilds the manifest from the current DB (e.g. after an exporter change).
- **Past-pruning is by local calendar day** (today onward), not rolling 24h.

## Working alongside other agents

Multiple agents may work this repo at once. To avoid stepping on each other:

- **Branch for big work.** Features touching the site or the jobs go on a
  feature branch + PR the human merges (see "Where to commit" above) —
  `main` touching `frontend/` deploys, so half-finished work there ships. Small
  changes go straight to `main`; pull first to avoid clobbering a parallel agent.
- **`events.json` is a conflict magnet.** It's regenerated (thousands of lines)
  on nearly every data change, so two agents that both re-scrape will conflict
  hard. Rules: (1) don't regenerate the manifest unless your task is *about* the
  data; (2) keep it in its own commit; (3) if you hit a conflict on it, don't
  hand-merge — take one side, then re-run the export
  (`docker compose run --rm scraper python -m exporters.json_export --out /out/events.json`)
  to produce a correct manifest from the DB.
- **One scrape run at a time.** Scrapers share the one Postgres DB and the one
  `events.json`. Two concurrent runs interleave writes and races on the export.
  Coordinate who owns a run; others should hold off or use `--sources` to touch
  only their own source.
- **Stay in your lane.** A scraper change touches `service/scrapers/<x>.py` + its
  test + maybe `sources.txt`/`main.py`. A frontend change touches
  `frontend/index.html`. These rarely conflict — conflicts almost always mean
  two agents touched shared files (`main.py` SCRAPERS list, `events.json`).
- **Leave a trail.** Note non-obvious decisions in the commit body or the
  relevant doc so a parallel agent (or the next session) doesn't re-derive them.

## Current focus

Shipped: AI tagging, scheduled per-source CI scraping (Neon, auto-merged data
PRs, failure alerts), email submissions, and cross-source fuzzy dedup. Ongoing: scaling the source list (candidates in
`future-sources.md`) and pruning source noise (e.g. SFPL non-events). Planned
work, including the frontend payload wall as the manifest grows, is in
`future-features.md`.
