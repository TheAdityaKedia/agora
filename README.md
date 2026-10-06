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
│   ├── vendor/                 # Vendored client libs (MiniSearch, MapLibre GL; pinned)
│   └── events.json             # Manifest the scraper writes, the page reads
├── .github/workflows/          # CI
│   └── deploy-pages.yml        # Publishes frontend/ to GitHub Pages
├── docker-compose.yml          # Postgres + API + scraper for local runs
├── CONTRIBUTING.md             # How to add a source (scraper) + write feature specs
├── feature-specs/              # Design specs for features not yet built
├── future-features.md          # Planned features, each linked to its spec
├── future-sources.md           # Candidate sources + sources that need fixing
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

`.github/workflows/scrape.yml` runs daily (3:23am PT, with a 7:23am backup slot
that skips if the first run happened — GitHub sometimes drops scheduled runs)
and on demand. Every source
scrapes on **its own runner** (speed, a fresh IP per source, fault isolation);
one merge job then saves all results into Neon Postgres in `sources.txt` order,
classifies new shows, exports `events.json`, and ships it as a bot PR
(`data/refresh-<run_id>`) that auto-merges when the guard passes (manifest
parses; event count ≥ 70% of `main`'s), then triggers the Pages deploy. A
failed or timed-out source keeps its rows from earlier runs. Stage code:
`service/ci.py`.

```bash
gh workflow run scrape.yml                              # full run
gh workflow run scrape.yml -f sources="citylights.com"  # one source
```

Runs from a branch other than `main` use the Neon `ci-test` branch,
ship into a throwaway `ci-sandbox/<run_id>` branch, and never deploy — safe for
testing workflow changes.

**One-time setup**

1. **Neon** — a project with a `production` branch (DB) and a `ci-test` child
   branch (test runs).
2. **AWS (Bedrock tagging)** — in the external AWS account: an IAM OIDC provider
   for `token.actions.githubusercontent.com` (audience `sts.amazonaws.com`), and
   a role (`AgoraGitHubBedrock`, account `978355607698`) trusted only for
   `repo:TheAdityaKedia/agora:environment:production` — jobs that declare an
   `environment:` get that OIDC subject instead of `ref:…`, and `production` is
   main-only — allowing just `bedrock:InvokeModel` on the Haiku 4.5 global
   inference profile + foundation model used in `service/classify.py`. A $5/mo
   AWS Budget (`agora-monthly`) emails on 80% actual / 100% forecast.
   Without the role, runs still ship — new shows are just untagged.
3. **Secrets, scoped by GitHub Environment** (Settings → Environments):
   - `production` — deployment branches restricted to `main`, so no other
     branch's workflow can read these: `DATABASE_URL` (Neon production, pooled,
     `sslmode=require`), `AWS_ROLE_ARN`, `AWS_REGION`.
   - `ci-test` — any branch: `DATABASE_URL` (Neon `ci-test` branch, with its
     own role password — not production's).
   - Repo-level: `ZYTE_API_KEY` (optional; scrape jobs only).

   ```bash
   gh secret set DATABASE_URL --env production   # value on stdin
   gh secret set AWS_ROLE_ARN --env production
   gh secret set AWS_REGION   --env production --body us-east-1
   ```
4. **Repo setting** — Settings → Actions → General → *Allow GitHub Actions to
   create and approve pull requests*.
5. **First run on `main` must be a full run** (the drop guard compares against
   `main`'s manifest, so a filtered run against a near-empty DB would be blocked).

**Failure alerts** — if any source hard-fails (error, crashed/timed-out job,
save error, no scraper) or returns 0 events, the run opens a **Scrape
failures** issue (or comments on the open one) that @mentions the repo owner —
GitHub emails you — and the run is marked failed. Data still ships first. A
clean run closes the issue. Sources in `service/data/local_only_sources.txt`
are never alerted on. Test runs from other branches use a separate
`Scrape failures (test run on <branch>)` issue.

**Sources blocked from GitHub** — a few sources fail from GitHub's datacenter
IPs (listed in `service/data/local_only_sources.txt`; details in
`future-sources.md` → "Sources that need fixing"). Scrape them from your own
machine straight into Neon; the next CI run ships them:

```bash
scripts/scrape-to-neon.sh --blocked          # the known-trouble sources → Neon
scripts/scrape-to-neon.sh --blocked --ship   # ...and dispatch a CI run to ship now
scripts/scrape-to-neon.sh gamh.com           # any sources (substring match)
```

It refuses to run while a `scrape.yml` run is in progress on `main` (one
writer at a time) and skips tagging (CI tags new shows).

**Operations** — when a scraper's *output* changes (not just new events), its
stale rows live on in Neon (saves never update): delete them in the Neon SQL
editor before the next run, e.g. `DELETE FROM events WHERE sources->>0 = '<NAME>';`.

**Tagging budget and re-tagging** — the merge job tags new or stale shows
8 at a time for at most 15 minutes, then saves what it has; the rest wait for
the next run and keep their old tags meanwhile. The log and the data PR show
how many were tagged, failed, left, and how many calls Bedrock **throttled**
(botocore retries those quietly, so a throttled run otherwise just looks
slow). To catch up without scraping (after a taxonomy bump, or a throttled
run), run **Re-tag events** (`retag.yml`; `gh workflow run retag.yml -f
budget_minutes=40`): it tags upcoming events already in Neon for up to 55
minutes, re-exports and ships through the same data PR path. Run it again
until its PR says 0 left.

**Duplicates across sources** — saves merge a new event into an existing row
from a *different* source at the same start time when the titles match after
normalization and the locations don't disagree (`service/dedup.py`). The
earlier source in `sources.txt` keeps the row. To merge duplicates already in
the DB: `python dedupe_existing.py` (dry run) then `--apply`.

**Venues and areas** — the merge job resolves every upcoming event's
location to a venue in `service/data/venues.json` (via
`venue_locations.json`), looking new ones up on OpenStreetMap (≤50 per run),
and the export joins `region`/`venue` onto each event for the site's Area
filter. A location is assigned only when independent signals agree. For a
string the rules can't place, Claude Haiku (≤20 calls per run) proposes a
name and address to look up; the map result must still match the source's
own text, so the model can find a place but never decides one alone. Anything
else waits in the **Places to review** issue, with the model's suggestion if
it had one (updated each run, closed when
empty; a comment @mentions you only when something new is waiting). To
resolve one, edit `service/data/venue_locations.json` in GitHub's editor:
replace its `pending` entry with `{"venue": "<id>"}`, `{"place": "none"}`
or `{"place": "online"}`, or add a venue to `venues.json` and point the
string at it; `cd service && python -m places validate` checks an edit, and
the next run applies it. The run goes red (after shipping) if more than 3% of
upcoming events have an unresolved location, more than 20 strings go pending
at once, or the venue files fail validation (that run ships no areas rather
than wrong ones). Design: `feature-specs/venues.md`.

## Event submissions by email

Anyone with the address **agora.bayarea@gmail.com** can email events in — the
address is the gate; submissions publish automatically as source
**"Community submissions"**. `.github/workflows/ingest-email.yml` runs hourly
(at :41, and on demand) and calls `python -m ingest.run` (`service/ingest/`).

**What it reads**, per email:
- **Mostly links** (≤400 chars of other text): each link (≤10) is resolved —
  Momence session API, Partiful page data (private events allowed: a link sent
  to us is consent), any page's schema.org `Event` JSON-LD, else the page text
  through Claude Haiku. If no link yields an event, the email text is read too.
- **Longer text** (typed details, forwarded newsletters): known-platform links
  (Momence, Partiful, Eventbrite, Luma; ≤20) are resolved exactly, then one
  Haiku call over the text (HTML body if the plain part is a stub).
- **Images** (≤5, ≤10 MB): one Haiku call each, which also classifies the image.

**What gets published**: title + date + start time required; a location that
names somewhere outside the Bay Area is rejected; past events dropped; weekly
series expanded 8 weeks (DST-safe); parts of one night (classes then a party)
are one event; phone numbers and emails are scrubbed from descriptions.
**Images** become the event picture only if safe: a designed flyer, a photo of
a flyer cropped to the flyer, or the flyer embedded in a screenshot (an
Instagram post) cropped out — snapped to the post image's straight edges — and
only after a final Claude Sonnet 4.5 check finds a clean flyer with no app UI,
identifiable person, or private contact details. A screenshot itself (chat,
DM, app UI) is never published. Safe images go to the private S3 bucket
`agora-submissions-978355607698`, served via CloudFront
(`d3ao3t7o4qlnbg.cloudfront.net`), EXIF stripped.

**Replies are failure-only**: the sender gets a short list of what couldn't be
added and why (no date/time, already happened, not Bay Area, couldn't read a
link). Links that load but aren't events (signatures) are ignored unless
nothing in the email was an event. Never replies to auto-replies, bounces,
no-reply addresses, or itself. Each email gets a Gmail label:
`agora/processed`, `agora/partial` (some failed), or `agora/failed`.

**Limits**: 50 emails/run, 20 events/email, 20 events/sender/day (counted by a
keyed hash in Neon — addresses are never stored). Nothing about senders goes
into logs, PRs, the DB, or the manifest.

**Operations**
- **Pause**: set environment variable `EMAIL_INGEST=off` (Settings →
  Environments → production).
- **Block a sender**: a Gmail filter "from X → skip the inbox".
- **Re-process an email**: remove its `agora/*` label in Gmail (labels show per
  conversation — use separate emails/subjects when testing).
- **Remove a submission**: in the Neon SQL editor,
  `DELETE FROM events WHERE sources->>0 = 'Community submissions' AND title = '…';`
  (check with a `SELECT` first).
- **Alerts**: a crash (Gmail login, Bedrock), or a problem the job works
  around so data still ships (tagging skipped, image upload error), opens or
  comments on an **Email ingest failures** issue that @mentions you (GitHub
  emails you) and turns the run red. The next clean run closes it. Per-email
  problems (no date, blocked link) aren't alerts — the sender gets a reply.
  A heartbeat in the daily scrape run also alerts (same issue) if the email
  job hasn't succeeded on `main` in 9 hours — GitHub can silently drop
  scheduled runs, and in practice honors only 3–6 of the 24 hourly slots a
  day, so the window allows for that rather than alerting on it.

**Setup** (done; for a rebuild): Gmail account with 2-Step Verification + app
password; secrets in environments `production` and `ci-test`:
`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `SUBMISSION_HASH_KEY`,
`SUBMISSION_IMAGE_BUCKET`, `SUBMISSION_IMAGE_BASE_URL` (plus the AWS role
secrets); the Bedrock role allows Haiku 4.5 + Sonnet 4.5 and `s3:PutObject` on
the bucket's `img/*`; AWS Budget `agora-monthly` is $10.
