# Agora — Development Notes

High-level map of what Agora is, how it's built today, the decisions behind it,
and where it's going. For the scraper-authoring playbook see `CONTRIBUTING.md`;
for per-feature designs see `feature-specs/`.

## Vision

Agora is the one place to find out what's happening in your city. Point it at
the venues and organizers you already care about — bookstores, theaters, dance
companies, galleries, music halls — and get a single unified, searchable,
shareable calendar back. No checking twenty websites; no missing events.

Long term: any website can be a source; events flow in from scrapers, email,
and flyer images; every event is auto-tagged by kind and cost; and any filtered
view is a shareable link.

## Where we are today

A working end-to-end pipeline serving a live public calendar:

- **~24 sources**, ~29 scraper modules (some are shared libraries, not sources).
  Bookstores, theaters, music venues, dance companies, museums, a public
  library system, and platform-backed organizers (Eventbrite, Luma).
- **~2,600 upcoming events** in the published manifest.
- **Static-site frontend** on GitHub Pages — a single self-contained
  `index.html` (inline CSS/JS, no build step) that reads `events.json`.
- **AI tagging** — every show is classified on two axes (**type** = format,
  **topic** = interest) + cost by an LLM (Amazon Bedrock, Claude Haiku), cached
  per show in a committed file. The frontend has type/topic filters and
  tag-aware search. See "AI tagging" under Design decisions.
- **Client-side search** (vendored MiniSearch): typo-tolerant, ranked, composes
  with source/date/location/type/topic filters; also matches tag labels.
- **Shareable URLs** — every filter (search, sources, dates, location, type,
  topic) round-trips through the query string; `?dates=today|week|month` resolve
  live so a bookmark always shows current happenings.
- **Deploys on push to `main`** touching `frontend/` via `deploy-pages.yml`.

Still **planned, not built**: email/flyer ingestion, any CDK/persistent-AWS
infrastructure. (Bedrock is used for tagging via mounted creds, not deployed
infra.) The earlier plan to run on AWS with a React frontend was superseded by
the simpler static-site + Docker approach below.

## Architecture

A small, decoupled pipeline. Each stage is independently testable.

```
sources.txt ──▶ scrapers (concurrent) ──▶ Postgres ──▶ JSON export ──▶ frontend/events.json ──▶ GitHub Pages
```

1. **Scrape** — `service/main.py` reads `service/data/sources.txt` and dispatches
   each URL to the first scraper whose `matches()` accepts it. Scrapes run in a
   thread pool (`SCRAPER_WORKERS`, default 6) — I/O-bound, so threads give real
   speedup; the cap bounds simultaneous headless-Chromium instances and
   per-source rate-limit pressure. **Each source is isolated**: a scraper that
   raises is logged and skipped, and the run continues to export (see Design
   decisions).
2. **Store** — events land in Postgres. Dedup keys on `(url, start_time)` then
   `(title, start_time)`; `start_time` is always part of the key, so a show
   playing many nights is kept as one row per performance. Cross-source
   duplicates merge their `sources` list. The DB is an **ephemeral working
   store** — safe to wipe; saves *skip* existing rows, they don't update them.
3. **Export** — `exporters/json_export.py` writes upcoming events to
   `frontend/events.json` (the durable artifact). Past events are pruned by
   local calendar day (default: today onward).
4. **Serve** — the static page fetches `./events.json` on load, renders events
   grouped by day, builds the search index, and applies any URL filters.

**Strategy pattern.** A scraper is a module exposing `matches(url)`,
`scrape(url)`, `SOURCE`, and `NAME`. Adding a source = write the module, append
its URL to `sources.txt`, register it in `main.SCRAPERS`. No conditionals to
edit. Shared libraries (`scrapers/eventbrite.py`, `scrapers/luma.py`,
`scrapers/performances.py`, `scrapers/browser.py`) let thin per-source wrappers
reuse the hard parts.

**Runtime.** Docker Compose: Postgres + FastAPI (read API over the DB) +
scraper. Local dev needs only a Docker runtime (Docker Desktop or Colima). The
API exists for querying the DB but the *published site* is driven entirely by
the committed `events.json`, not a live backend.

## Design principles

- **Start minimal.** Add infrastructure only when the simpler thing breaks. The
  site is static files on Pages, not a running service, because that's all it
  needs to be.
- **Strategies, not conditionals.** Each source is a module; dispatch is by
  `matches()`. The pipeline never grows a per-source `if`.
- **Structured data over DOM scraping.** Prefer a JSON API > schema.org JSON-LD
  > ICS feed > paginated listing > DOM. Each rung is more stable. (See
  `CONTRIBUTING.md`.)
- **AI at the edges (when it arrives).** Deterministic parsing stays
  deterministic; an LLM will only turn unstructured title/description text into
  structured tags, classified once and cached.
- **The manifest is the source of truth for the site.** The DB is a scratch
  space you can rebuild anytime; the committed JSON is what ships.

## Design decisions

**Per-source failure isolation.** `main.run()` wraps each source's
scrape+save in try/except: one scraper raising (markup changed, site down, a
WAF block surfacing as an exception) is logged and skipped, and the run still
reaches the export step. Before this, an unhandled exception propagated out of
`future.result()` and aborted the whole run *before the manifest was written* —
so one bad source produced no output at all. This fragility scales badly as the
source list grows (one flaky source in 100 breaks every run), so it was fixed
before onboarding more sources.

**Deduplication.** Two layers: `(url, start_time)`, then `(title, start_time)`.
`start_time` is always in the key so many performances of one show (which often
share a single show URL) stay distinct. A third, fuzzy/semantic layer is
deferred to when AI ingestion lands — OCR'd flyer titles will need it.

**Location as one string.** Stored unstructured (`"Venue, Street, City, CA"`).
Splitting into venue/address/city is deferred until we have enough
cross-source data to know the right schema.

**One event per performance — but never fabricated.** Expand a run into
per-showing events only when the source exposes structured per-performance data
(JSON-LD `subEvent`, ticketing API, etc.). A multi-week run with no
per-occurrence structure stays one event; guessing dates produces wrong data.

**Client-side search, client-side everything.** Search runs in the browser over
the manifest (MiniSearch). No server. The searchable text is already in
`events.json`, so no precomputed index is shipped — see `feature-specs/search.md`.

**Past-pruning by calendar day, not rolling 24h.** The exporter keeps events
whose *local* day is today or later, so a 9am event stays visible all day
rather than aging out mid-morning.

## Scaling considerations (as the source list grows)

We have ~95 more candidate sources queued in `future-sources.md`. Adding them
naively hits a few walls, in rough priority order:

1. **Failure isolation — DONE.** Prerequisite for any growth; see above.
2. **Frontend payload is the next wall.** The browser downloads the entire
   `events.json` and builds the search index on load. At ~2,600 events that's
   fine (<500ms index build); at ~10k it becomes a multi-MB download, a 1–2s
   index build, and huge per-scrape git diffs. When events cross ~5k, ship a
   prebuilt index and/or paginate/lazy-load the manifest (escalation point noted
   in `feature-specs/search.md` §4).
3. **Runtime.** Concurrency is fixed at 6 regardless of source count, so wall
   time ≈ total work ÷ 6, bounded by the slowest sources (SFPL's ~1,100 detail
   fetches, WAF-stalled sources like SFJAZZ). More sources → longer runs; the
   detail-fetch-heavy ones dominate.
4. **Platform scrapers beat per-venue ones.** Many venues share a ticketing
   backend (Veezi for indie cinemas, Eventive for film fests, VBO, Tixr). One
   platform scraper unlocks many venues — the model already used for Eventbrite
   and Luma. Prefer these over N bespoke scrapers.
5. **Onboard in tiers, not all at once.** Start with the highest-value sources
   that expose clean structured data (museums with JSON-LD, City Arts &
   Lectures, major bookstores); leave WAF-hardened sources for last or skip.
6. **IP reputation.** Many sources scraped from one CI IP draws more Cloudflare
   403s. Some sources may only ever work from a fresh runner IP.

## Roadmap

**Shipped:** AI tagging (two-axis type+topic + cost, Claude Haiku, per-show
cache, frontend type/topic filters + tag search). The as-built design differs
from the original `feature-specs/tagging.md` draft (that draft is single-axis /
Nova) — the code is the source of truth: `service/taxonomy.py`, `classify.py`,
`classifications.py`.

Near-term, in likely order:
- **Onboard queued sources in tiers** from `future-sources.md`, structured-data
  first; build **platform scrapers** (Veezi, Eventive) where they unlock many
  venues at once. Each source also needs a `source_profiles.json` tagging prior.
- **Address the frontend payload wall** before the manifest gets large.
- **`reclassify` CLI** — force re-tagging of stale/all shows (today a taxonomy
  version bump or deleting the cache forces it).
- **Email / flyer ingestion** — forward an email or flyer screenshot to a
  monitored inbox; parse (LLM for unstructured images) and add to the calendar.
  Also the path for login-gated sources.
- **Scheduled scrapes** — currently run on demand; automate on a cadence.
