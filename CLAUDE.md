# CLAUDE.md — start here

Orientation for an agent working on Agora. This is a **router**, not a manual —
it points you at the right doc and lists the operating rules and sharp edges the
other docs don't cover. Read the linked doc for depth; don't duplicate it here.

## What Agora is (30 seconds)

An SF Bay Area event aggregator. Scrapers pull events from ~24 venue/organizer
sites into Postgres; an exporter writes `frontend/events.json`; a static
single-page site on GitHub Pages reads that JSON and renders a searchable,
filterable, shareable calendar. No live backend serves the site — the committed
`events.json` is the artifact that ships.

Pipeline: `sources.txt → scrapers (concurrent) → Postgres → events.json → Pages`

## Where to read for your task

| Task | Read |
|------|------|
| Understand the system, decisions, roadmap, scaling | `DEVELOPMENT.md` |
| Add or fix a **scraper** (the most common change) | `CONTRIBUTING.md` |
| Run it locally / deploy / update the site | `README.md` |
| Frontend search design (MiniSearch, ranking) | `feature-specs/search.md` |
| AI tagging design (planned, not built) | `feature-specs/tagging.md` |
| Candidate sources to onboard next | `future-sources.md` |
| Add a **new subsystem/feature** (not a scraper) | write a spec in `feature-specs/` first — see `CONTRIBUTING.md` |

## Layout

- `service/` — Python backend. `scrapers/` (one module per source + shared libs
  `eventbrite.py`, `luma.py`, `performances.py`, `browser.py`), `exporters/`,
  `main.py` (pipeline entry), `api.py` (read API), `tests/`, `data/sources.txt`.
- `frontend/` — `index.html` (self-contained, inline CSS/JS, no build),
  `vendor/` (pinned MiniSearch), `events.json` (the manifest).
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
  source of truth for the site — `events.json` is.

## Sharp edges (things that have actually bitten)

- **`--exclude`/`--sources` are plain substring matches.** `sfpl` also matches
  `sfplayhouse`; use `sfpl.org`. Check for collisions before trusting a filter.
- **Cloudflare/WAF sources fail from local/CI IPs.** SFJAZZ, GAMH, Fillmore,
  Green Apple 403 after a few hits regardless of stealth tricks. Keep listing
  data; don't over-invest in beating the anti-bot (see `CONTRIBUTING.md` →
  "When to give up"). Stealth patches were tried and *reverted* — they broke
  rendering.
- **Frontend JS runs one big IIFE** — mind temporal-dead-zone ordering. A `const`
  referenced during page-load init (e.g. from `readStateFromUrl`) must be
  declared before that code runs, or a shared `?q=` link hangs on "Loading…".
- **Re-exporting doesn't need a scrape:**
  `docker compose run --rm scraper python -m exporters.json_export --out /out/events.json`
  rebuilds the manifest from the current DB (e.g. after an exporter change).
- **Past-pruning is by local calendar day** (today onward), not rolling 24h.

## Current focus

Scaling the source list (~95 candidates queued in `future-sources.md`).
Per-source failure isolation is done; the next wall is the frontend payload as
the manifest grows — see `DEVELOPMENT.md` → "Scaling considerations."
