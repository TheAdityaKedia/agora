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
| Understand the system, decisions, roadmap, scaling | `DEVELOPMENT.md` |
| Add or fix a **scraper** (the most common change) | `CONTRIBUTING.md` |
| Run it locally / deploy / update the site | `README.md` |
| Frontend search design (MiniSearch, ranking) | `feature-specs/search.md` |
| AI tagging (built) — taxonomy, classifier, cache, filters | code: `service/taxonomy.py`, `classify.py`, `classifications.py`; ⚠️ `feature-specs/tagging.md` is the ORIGINAL draft and is stale (single-axis / Nova) — the built version is two-axis (type+topic), Claude Haiku, with `source_profiles.json` |
| Candidate sources to onboard next | `future-sources.md` |
| Add a **new subsystem/feature** (not a scraper) | write a spec in `feature-specs/` first — see `CONTRIBUTING.md` |

## Layout

- `service/` — Python backend. `scrapers/` (one module per source + shared libs
  `eventbrite.py`, `luma.py`, `performances.py`, `browser.py`), `exporters/`,
  `taxonomy.py` + `classify.py` + `classifications.py` (AI tagging), `main.py`
  (pipeline entry), `api.py` (read API), `tests/`. `data/` holds `sources.txt`,
  `taxonomy.v1.json`, `source_profiles.json` (tagging venue priors), and the
  committed `classifications.json` (tag cache).
- `frontend/` — `index.html` (self-contained, inline CSS/JS, no build),
  `vendor/` (pinned MiniSearch), `events.json` (the manifest — carries the
  taxonomy block + per-event `types`/`topics`/`cost`).
- `.github/workflows/deploy-pages.yml` — deploys `frontend/` on push to `main`.

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
- **The DB is ephemeral; the manifest is durable.** Never treat DB state as the
  source of truth for the site — `events.json` is. Same for tags:
  `classifications.json` is the durable committed cache, not the DB.
- **AI tagging (classify step in `run()`):** calls Bedrock (Claude Haiku); needs
  AWS creds (the compose scraper mounts `~/.aws` — refresh on host first) or it's
  skipped (`--no-classify` to skip explicitly). Classifies only cache misses
  (new shows / stale taxonomy version), keyed per `(source, title)`. Adding a
  source ⇒ also add its `source_profiles.json` line (see `CONTRIBUTING.md`).

## Sharp edges (things that have actually bitten)

- **`--exclude`/`--sources` are plain substring matches.** `sfpl` also matches
  `sfplayhouse`; use `sfpl.org`. Check for collisions before trusting a filter.
- **Cloudflare/WAF sources: work the ladder in `CONTRIBUTING.md` → "When to
  give up"** before declaring a source dead. Two looked hopeless and are solved:
  **SFJAZZ** via an un-fronted origin API (`sfjazz.py`), and **Green Apple** via
  full Chromium (`browser_context(full_chromium=True)`) + its robots.txt
  crawl-delay + a fresh context on challenge. Both are free. Gotcha: a scraper
  can **pass on your Mac and fail in Docker** (Playwright's default headless
  shell gets caught by Cloudflare in the Linux container), so always verify in
  Docker. GAMH, Fillmore, and Great Star's TicketTailor pages still 403 but
  haven't been tried with full Chromium yet. Fingerprint/stealth patches were
  tried and *reverted* (they broke rendering). Don't go there.
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

- **Branch per agent/task.** Don't all commit to `main`. Cut a feature branch,
  push it, open a PR; let the human merge. `main` touching `frontend/` deploys,
  so uncoordinated pushes to `main` also ship half-finished work.
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

AI tagging shipped (two-axis type+topic, Claude Haiku, cached; frontend
filters + tag search). Ongoing: scaling the source list (~95 candidates in
`future-sources.md`) and pruning source noise (e.g. SFPL non-events). The next
structural wall is the frontend payload as the manifest grows — see
`DEVELOPMENT.md` → "Scaling considerations."
