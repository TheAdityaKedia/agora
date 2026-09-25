# Contributing to Agora

This guide is mostly about the thing we do most often: **adding a new event
source (a scraper).** It captures what we've learned so you don't reinvent the
wheel each time. Read it before writing a scraper — most of the hard-won
lessons below were paid for once already.

## Changing the tag taxonomy

Adding/renaming/removing a **type** or **topic** has a specific checklist —
including a coupling that's easy to miss (the frontend `GROUP_HUE` map, and
whether existing classifications need re-tagging). The authoritative runbook
lives in the module docstring of **`service/taxonomy.py`** — read it before
editing `service/data/taxonomy.v*.json`.

## Larger features: write a spec first

Adding a scraper is a small, well-worn change. Anything bigger — a new
subsystem or a cross-cutting feature (AI tagging, email ingestion, a scheduler)
— gets a **design spec** in `feature-specs/<feature>.md` *before* any code.

Why: these features involve real design decisions (data model, storage,
versioning, where it slots into the pipeline) with trade-offs worth settling —
and recording — up front, so we don't re-litigate them mid-implementation or
lose the *why* behind a choice.

A spec should capture:
- **Goal & non-goals** — what's in scope for this phase, what's deliberately not.
- **Design decisions with rationale** — not just what, but *why* (e.g. "the
  classification cache is a committed JSON file, not a Postgres table, because
  the DB is an ephemeral working store").
- **Data model / storage / interfaces** — concrete shapes (tables, files, JSON).
- **How it fits the pipeline** — where the new step runs; how it stays
  incremental/idempotent.
- **Phasing** — split into ordered, shippable chunks (backend before frontend).
- **Testing plan** and **open questions to verify**.

Flow: draft the spec → review it → turn it into an ordered, TDD-able
implementation plan → build. `feature-specs/tagging.md` is the worked example.
Keep specs versioned in git alongside the code they describe.

## The scraper contract

Each source is a **strategy module** in `service/scrapers/<name>.py` exposing:

- `matches(url) -> bool` — does this scraper handle `url`?
- `scrape(url) -> list[RawEvent]` — fetch + parse into events.
- `SOURCE` — the domain (e.g. `"sfjazz.org"`).
- `NAME` — the human-readable venue label shown on the frontend (e.g.
  `"SFJAZZ Center"`). This is what gets persisted as the event's source, so keep
  it stable — the manifest, DB, and UI all key on it.

Register the module in `main.SCRAPERS`, and add the source URL to
`service/data/sources.txt`. No conditionals to edit — dispatch is by `matches()`.

**Also add a one-line venue profile** to `service/data/source_profiles.json`,
keyed by the exact `NAME` string. This is the prior the AI tagger leans on when
an event's description is thin or empty — a film at a rep cinema still tags as
a screening, a show at a jazz hall as a concert. One sentence: what the venue is
+ what kinds of events it hosts (e.g. `"Roxie Theater": "SF repertory/indie
movie theater; film screenings (new releases, classics, repertory)."`). Every
source should have an entry — see the existing ones for tone.

`RawEvent` (see `scrapers/base.py`): `title`, `start_time` (tz-aware, **UTC**),
`location`, `url`, `description`, `image_url`. Keep `parse*` functions **pure**
(operate on already-fetched HTML/JSON) so they're testable without the network;
put fetching in `scrape()`.

## Investigate before you code

Ninety percent of a good scraper is finding the *right data source*. Before
writing a parser, spike the page:

0. **Check if it's a platform we already handle** (see *Reusable platform
   libraries* below). Most new sources — especially venues that outsource their
   calendar/ticketing — turn out to run on Eventbrite, Luma, Squarespace,
   WordPress + The Events Calendar, Elfsight, Ludus, OvationTix, or a plain ICS
   feed. If so, the scraper is a ~15-line wrapper. Follow the venue's own
   "tickets"/"calendar"/"schedule" links — the platform often lives on a
   **different domain** (Litquake's schedule is on `litquake2026.sched.com`, The
   Marsh's on `themarsh.ludus.com`), so don't stop at sniffing the root domain.
1. **View source / meta** — `<meta name="description">`, `og:description`.
2. **JSON-LD** — `<script type="application/ld+json">`. Look for `Event` /
   `TheaterEvent`, a `subEvent[]` array (per-performance!), `offers`, and a
   `description`.
3. **The network tab is your friend** — most modern venue widgets are a thin
   shell over a JSON API. Render the page in a headless browser and capture XHR
   responses; search them for an event id or `"description"`. This is how we
   found Black Bird's Mahina API, SF Playhouse's VBO `@graph`, and OvationTix's
   `performance` endpoint. A clean JSON API beats DOM scraping every time — and
   an embedded JSON blob counts too (Ludus ships every showtime inside an
   Alpine `x-data="…JSON.parse('…')…"` attribute; the Elfsight widget calls a
   `/api/events` endpoint). An opaque SPA is usually a *good* sign: the data is
   structured, you just have to find where it loads.
4. **ICS feeds** — Squarespace and others expose `?format=ical`; sometimes the
   cleanest per-occurrence source (via `RRULE`).
5. **Get the *whole* list, and don't trust page HTML for IDs.** Two SPA traps:
   lazy-loaded calendars serve only the first page in the initial HTML (Luma's
   JSON-LD had 15 events; the `get-items` API had 97) — confirm the count and
   page the API, don't ship a truncated calendar. And a site's server-rendered
   HTML can be *inconsistent* — Luma sometimes returns a bare JS shell with no
   calendar id or JSON-LD — so resolve identifiers through an API
   (`api.luma.com/url?url=<slug>`) rather than depending on the page markup.

### Data-source priority ladder

Prefer, in order: **structured JSON API > schema.org JSON-LD > ICS feed >
paginated listing > DOM scraping.** Each rung is more stable and richer than
the one below it. Only scrape the rendered DOM when nothing structured
exists.

**Paginated listings** (Drupal `?page=N`, WordPress `/page/N/`) sit above ad
hoc DOM scraping: the pagination is the source's own contract for iterating
its full inventory. Walk pages sequentially until the "last page" link says
stop, or events start landing past your look-ahead horizon. Reference: `sfpl.py`
reads the highest `?page=` in the pagination footer's `Last »` link and walks
to it.

## Reusable platform libraries

Many venues outsource their calendar/ticketing to the same handful of
platforms, so we have **shared libs** — a new source on a known platform is a
thin wrapper (`SOURCE`, `NAME`, the platform id/URL, `matches()`, and a one-line
`scrape()` that delegates). Check these first:

| Platform | How to detect | Shared lib → entry point | Example wrappers |
|----------|---------------|--------------------------|------------------|
| **Eventbrite** | `eventbrite.com/o/<org>` organizer page (or `/e/…-tickets-<id>` links) | `eventbrite.py` → `scrape_organizer(url)` | `phoenix.py`, `neofuturists.py` |
| **Luma** | `luma.com/<slug>` / `lu.ma` | `luma.py` → `scrape_calendar(url)` (resolves the calendar api_id via the page or the `/url` endpoint, then pages the `get-items` API) | `bigbrainbay.py`, `thecommons.py`, `readingrhythms.py` |
| **Squarespace Events Collection** | `article.eventlist-event` in the events page HTML | `squarespace_events.py` → `scrape_collection(url, fallback_location=…)` | `balboa.py`, `fourstar.py`, `medicinenightmares.py` |
| **WordPress + The Events Calendar (Tribe)** | `GET /wp-json/tribe/events/v1/events` returns JSON | `tribe_events.py` → `scrape_events(site_base, fallback_location=…)` | `birdbeckett.py`, `oaklandartmurmur.py` |
| **Elfsight Event Calendar** | `elfsight` in page; widget XHR to `widget-data.service.elfsight.com/api/events?source=<id>` | `elfsight_events.py` → `scrape_events(source_id, …)` | `riptide.py` |
| **iCal / ICS feed** | any `.ics` link (Sched `all.ics`, Squarespace `?format=ical`) | `ics.py` → `scrape_ics(ics_url, fallback_location=…)` | `litquake.py` (Sched) |
| **Ludus** (ticketing) | `<org>.ludus.com/calendar` (403s plain requests; renders in a browser) | `ludus.py` → `scrape_calendar(url, fallback_location=…)` | `themarsh.py` |
| **Elfsight (settings mode)** | Elfsight widget whose `/api/events` 404s; the embed's `core.service.elfsight.com/p/boot` response carries `settings.events` | `elfsight_events.py` → `scrape_widget_settings(widget_id, page_url, …)` | `booksinc.py` |
| **IndieCommerce** (ABA bookstores, Drupal) | `indiecommerce` in page; `/events/YYYY/MM` month listings of `article.event-list` cards | `indiecommerce.py` → `scrape_events(site_base, fallback_location=…)` | `booksmith.py`, `bookpassage.py`, `noevalleybooks.py`, `mrsdalloways.py`, `bookshopwestportal.py` |
| **BookManager** (bookstore webstore SPA) | "You need to enable JavaScript"; XHRs to `api.bookmanager.com/customer/…` | `bookmanager.py` → `scrape_events(store_id, site_base, …)` | `tallyho.py` |

And a few **patterns** we reuse by copying rather than a shared lib:

- **OvationTix / AudienceView** — `ci.ovationtix.com/<clientId>`; hit
  `web.ovationtix.com/trs/api/rest/…` with a `clientId` header (no browser).
  Reference: `zspace.py` (joins `CalendarProductions` + `Production`).
- **VBO (`vbotickets`)** — a schema.org `@graph` in the page JSON-LD.
  Reference: `sfplayhouse.py`.
- **Shopify + Mahina events app** — a JSON API behind the storefront widget.
  Reference: `blackbird.py`.
- **Shopify "event products"** — events sold as products in a collection;
  `/collections/<c>/products.json` lists them, the date is only on the product
  page. Reference: `omnivore.py`.
- **Eventbrite links embedded on the venue's own site** — when the organizer
  page renders only a few events, collect `/e/<id>` links from the venue site
  and hand them to `eventbrite.scrape_event_urls`. Reference: `clios.py`.
- **Hand-written dates with no year** ("Tuesday, September 29th at 7pm") —
  `scrapers/datetext.py::parse_weekday_date` picks the year by weekday.
  References: `omnivore.py`, `fabulosa.py`.

Two recurring gotchas these libs handle, worth copying:

- **Bound geographically / off-topic** at the wrapper. Statewide Luma calendars
  mix in LA/San Diego events — filter to the Bay Area on `location` (titles
  don't reliably encode the city). Reference: `readingrhythms.py`.
- **Collapse recurring exhibitions.** Gallery/exhibition APIs (Tribe) often
  repeat one show once per day it's on view (150+ near-identical entries) —
  collapse repeated titles to the earliest occurrence. Reference:
  `oaklandartmurmur.py::collapse_by_title`. (Contrast: a weekly series like Bird
  & Beckett's jazz nights *should* stay one event per night.)
- **One page, many editions.** A recurring series (The Marsh's Tell It On
  Tuesday) has a *single* detail page that updates to the **next** edition's
  lineup. Don't stamp that edition-specific text on every date — enrich the
  soonest occurrence with the full page text and give later occurrences only
  the evergreen series blurb (truncate at edition markers like "Artist
  Biography" / "Featuring"). Reference: `themarsh.py::_evergreen` and its
  earliest-occurrence logic.

## Enriching from a second source

Ticketing calendars (Ludus, OvationTix) reliably carry **showtimes** but often
no synopsis or image; the venue's own site has those on a per-show page. When
that's the case, scrape the calendar for the schedule, then **match each show
to its detail page** and enrich (description, poster, a nicer URL). Reference:
`themarsh.py` (Ludus calendar → WordPress show pages).

Matching is the hard part — lessons, best method first:

- **Join on a shared id, not the title.** If the ticketing platform and the CMS
  both reference the same ticket id, match on it — it's exact. The Marsh
  calendar's ticket links carry a Ludus `show_id`, and each WP page embeds a
  Buy-Tickets link to the same id, so `themarsh.py` joins on it. This dissolves
  every title-matching failure at once: abbreviated slugs
  (`unique-derique-fll-whimsical`) and co-presentation aliases — the id proved
  "LABA's Name Game" *is* "Elissa Strauss's Name Game" (same show), a real match
  that title heuristics had *wrongly rejected* as a false positive.
- **Fall back to normalized-string containment** (lowercase, strip
  non-alphanumerics and a trailing year) for pages that don't expose the id.
  It matches `notjustjazz` ↔ "Not Just Jazz 2026" where token overlap is zero.
- **Token-overlap matching is fuzzy and false-positives** — only trust it on a
  small curated set (e.g. the homepage's featured shows); for a broad index use
  the id join or containment.
- **Discover pages from the sitemaps, newest-first.** The homepage lists only
  featured shows; `…/post-sitemap.xml` + `…/page-sitemap.xml` list them all
  (shows live both under `/shows_and_events/` and at the root). Order by
  `<lastmod>` (a current show was just updated) and stop once every calendar
  show resolves, so you fetch only a handful. Exclude stale archive paths (The
  Marsh's `/marshstream/` livestream pages).

## Listing vs. detail page

The calendar/listing page almost never has the full synopsis or the individual
showtimes — those live on each event's **detail page**. Expect to fetch the
detail page (which you usually already have the URL for) for descriptions and
per-performance data.

## One event per performance

Users browse by day, so a show that plays many nights should be **one event per
showing** — but only when the source exposes **structured per-performance
data**: a JSON-LD `subEvent[]`, an `offers[]` list, one `Event` block per
showing, or a ticketing API (OvationTix, VBO, Mahina). Use the shared
`scrapers/performances.py::expand_shows` helper to expand a run-level show into
performances with a graceful fallback.

**Never fabricate performances** from a run range ("Sep 12 – Oct 25"). A
multi-week run doesn't play every night, and guessing dates/times produces
wrong data — worse than one accurate opening-day entry. If a source only
publishes a range with no per-occurrence structure (Magic, Brava), leave it as
one event per run.

Reference implementations:
- **JSON API** → `blackbird.py` (Mahina), `sfplayhouse.py` (VBO `@graph`).
- **JSON-LD `subEvent`/`offers`** → `atgtickets.py`, `presidio.py`,
  `berkeleyrep.py`, `nctcsf.py`.
- **Rendered widget** → `actsf.py` (Tessitura `/performances`).

## URLs: link to the show, not the checkout

Every event's `url` should point at the **show/info page**, not a per-seat
ticketing deep link. Seat-selection links are a poor landing page and are often
*missing* for shows not on sale (→ unclickable events). Take the show URL from
the listing card / JSON-LD top-level `url`, and give every performance of a show
that same URL (`start_time` still makes each performance a distinct row).

## Descriptions

Fetch the detail page and extract the real synopsis. Lessons:

- Find the specific container — don't grab the whole body. Selectors we've used:
  `.s-prose` (A.C.T.), `div.text-nrml` (Presidio), `.left-content` (YBCA),
  `.artist-list` (Independent), `.bio` (Warfield), `.event-info` (SF War
  Memorial), `.vem-single-event-details` (NCTC), `.eventitem-column-content`
  (Magic), first `.w-richtext` (Palace).
- **Strip the noise** — credit lines ("By …", "Directed by …"), "Content
  Warning", "Runtime", booking notes, privacy banners, directions/parking.
- **Fall back gracefully** — keep the date/genre/presenter string when there's
  no synopsis; never crash on a missing block.
- **Preserve useful labels** — when replacing a thin description that carried a
  presenter ("San Francisco Opera"), prepend it: `"<presenter> · <synopsis>"`.
- Some sources genuinely have no blurb (music venues, Fillmore) — that's fine.
- **Bound the enrichment window** if the source's calendar goes far out and
  detail-page fetches are expensive — see "Detail-page fetches inside a single
  scraper" below.

## Dedup & keys

`main._find_duplicate` matches on `(url, start_time)`, then `(title,
start_time)`. `start_time` is always part of the key, so many performances can
share one show URL (Berkeley Rep, NCTC). The DB's partial unique index is on
`(url, start_time)`. (Classification, a separate concern, keys per *show* on
`(title, source)` — see `feature-specs/tagging.md`.)

## Dates & timezones

- Sources often give date-only or omit the year. Infer the year from context
  (current SF year; roll forward on a Dec→Jan month wrap) — see `blackbird.py`,
  `sfjazz.py`. When the source gives an absolute ISO datetime, use it (no
  inference needed) — always prefer that.
- Always normalize **source-local → UTC** before storing.

## Filter irrelevant content at scrape time

If a large fraction of a source's programming is off-target for the aggregator's
audience, filter it out in `parse()` before it hits the DB — don't add a metadata
field and hide it in the frontend. Reasons:

- Irrelevant events still compete for dedup slots and inflate the manifest.
- The DB is an ephemeral working store, so metadata filtering has to run on
  every export anyway.
- Frontend filtering pushes the "what am I aggregating" decision to the wrong
  layer.

Reference: `sfpl.py::_is_kid_only` drops storytime-only cards (babies /
elementary / middle-school-age with no adult-relevant audience class) at scrape
time. That's ~70% of SFPL's programming. Multi-audience events (a kid class +
`all-ages` or `families`) are kept.

**Tag-based filtering happens at export, not scrape time.** When the "off-target"
judgment depends on the AI tags (which don't exist until after classification),
filter in the exporter instead. Reference:
`exporters/json_export.py::_is_food_drink_only` drops events whose *sole* type is
`social/food-drink` (happy hours, lunch specials, tastings) after tags are
joined — while keeping events that merely touch food/drink in another format (a
cooking `workshop`, a food `talk`, a dinner + `performance`). Key on the type
being the *only* format, not on the topic, so genuine events aren't lost.

## When to give up

- **Hard bot protection** — if a page returns 403 even to our headless
  browser, work down this ladder **in order** before giving up. Each rung is
  cheaper and more durable than the next:
  1. **Is it only `requests` that's blocked?** Many sites 403 a bare `requests`
     call yet render fine in the browser (Ludus's calendar) — use `browser.py`.
  2. **Is there an un-fronted origin or internal API?** The WAF usually guards
     only the public hostname. Check `robots.txt`, sitemaps, and the XHRs the
     page makes for a staging/CDN-origin host or a JSON endpoint. `sfjazz.py`
     went 0 → 227 events this way (Adage `ace-api` on the origin host).
  3. **Behave like a real, polite browser** — see "Cloudflare managed
     challenges" below. Green Apple went 0 → 40 events this way, free, with no
     fingerprint spoofing.
  4. **Only then: the Zyte hosted fetch** (`scrapers/zyte.py`, below). Paid,
     last resort.
  If none of these work, keep the best available listing data and stop. **A
  CAPTCHA (e.g. Cloudflare Turnstile) is a hard stop**: it's an explicit
  "prove you're human" check, and we don't try to beat it. GAMH's Eventim event
  pages and Fillmore's Ticketmaster pages are in this bucket. Before giving up
  on a source, also check whether the listing is **paginated** ("Load more"
  buttons, `data-*-total-pages`). GAMH was silently at 12 of ~74 events for
  that reason, not because of any block.
- **Read `robots.txt` first, and honor it.** It's the one URL Cloudflare
  sites usually serve to anyone, and it tells you the owner's intent.
  Green Apple's sets `crawl-delay: 10` for all agents *and* bans AI crawlers by
  name. We scrape it with the owner's OK, at the stated crawl-delay. Honor it
  because it's the owner's rule, not as an anti-bot trick. (We never showed
  the delay mattered technically; see below.)
- **No structured data** — if there's no per-performance structure anywhere,
  don't fabricate it (see "One event per performance").

### Cloudflare managed challenges (rung 3)

Symptom: the first page loads (200) and later ones return **403 "Just a
moment..."**, a Cloudflare managed challenge, which never resolves no matter
how long you wait on the page. Three things fixed it for Green Apple, all
opt-in per scraper (reference: `greenapple.py`). #1 is the technical fix;
#2 is politeness; #3 handles the occasional challenge that still gets through:

1. **Full Chromium, not the headless shell** —
   `browser_context(full_chromium=True)`. Playwright's default headless mode
   launches `chrome-headless-shell`, a stripped build Cloudflare detects. The
   full binary in (new) headless mode passes. This was *the* fix.
   It explains a confusing symptom: **the same script passed on a Mac host but
   failed in Docker.** Same IP, same Chromium version. The difference is the
   machine the browser reports (Docker on a Mac is a small Linux VM: 2 cores,
   software `SwiftShader` GPU). Spoofing the GPU/platform/cores did **not**
   help; switching to full Chromium did, without spoofing anything. Don't
   reach for fingerprint spoofing (earlier stealth patches broke rendering and
   were reverted).
2. **Honor the site's crawl-delay** before *every* fetch, detail pages
   included (`CRAWL_DELAY_S = 10`, from their robots.txt). Careful with the
   evidence here: the only run blocked at 2.5s used the headless shell in
   Docker, which is also blocked at 10s. So the delay was never isolated as a
   cause. Full Chromium at a shorter delay would *probably* work. We keep 10s
   because it's their stated rule, and we don't test shorter delays against a
   site that asked for 10s.
3. **Fresh context on a challenge, retry once** — `_resilient_fetcher`.
   Cloudflare scores a *session*: one cookie jar gets challenged after ~13
   requests even at the polite pace, but a new context on the same IP passes at
   once. `browser.new_browser_context(context.browser)` gives you one. Retry
   once only; a second 403 propagates as `RateLimited` so the scraper stops.

Verify this **in Docker** (`docker compose run ...`), never only on your Mac:
the host passes things the pipeline won't.

### Last resort: the Zyte fetch backend (rung 4)

`scrapers/zyte.py` wraps [Zyte API](https://docs.zyte.com/zyte-api/), a hosted
fetch through rotating proxies plus Zyte's own anti-ban stack. No scraper uses
it today. It's kept for a source that beats rungs 1–3. It's metered per
request, so it comes last.

It's an **env-gated seam**, not a new scraper shape. The parser is untouched:

```python
if zyte.is_configured():                       # ZYTE_API_KEY in the env
    html = zyte.fetch_html(url, log=_log)
else:
    html = load_page_html(context, url)        # existing path, still works
```

Rules for using it:

- **Use `render=True` (the default)**, which asks for `browserHtml`. The cheap
  `httpResponseBody` tier does **not** clear a real Cloudflare challenge
  (Green Apple answered it with a 520 "Website Ban").
- **`fetch_html` raises `RateLimited`** (the same exception
  `browser.load_page_html` raises) when Zyte can't get through after retries,
  so a scraper's existing "blocked, stop early" branch works for either backend.
- **Still honor robots.txt's crawl-delay.** Zyte rotating IPs is a way around
  a rate limit, not permission to ignore it.
- **Bound it.** Each render costs credits and ~20s. Only route
  genuinely-blocked fetches through it, cap requests per run, and never put a
  high-volume enrichment loop behind it (SFPL's 800+ detail pages would cost
  ~37× everything else combined). Measured cost of a Green Apple run was ~15
  browser renders, i.e. low single-digit dollars a month daily.
- **Without a key nothing changes**: local runs, CI, and tests never touch the
  network or spend credits. The key lives in the gitignored `.env` (see
  `.env.example`); compose passes it to the scraper container.

## Playwright vs. requests

- Plain `requests` (with `scrapers.browser.BROWSER_UA`) for static / server-
  rendered pages — most detail pages.
- `scrapers.browser.browser_context` + `load_page_html(...)` for JS-rendered
  widgets or WAF-challenged pages (Cloudflare, Ticketweb calendars). Tune
  `wait_until` (`"load"` — `networkidle` often hangs on trackers) and
  `settle_ms` for late-rendering content.
- `scrapers.browser.browser_session()` when you need to hit many URLs on the
  *same* WAF-protected site: it reuses one browser process but gives you a
  **fresh Playwright context per URL** via `.fresh_context()`. Same-context
  back-to-back detail-page fetches on Cloudflare-protected sources (SFJAZZ)
  get 403 immediately; a fresh context per URL sidesteps it.
- Full scrapes run **concurrently** across sources (`main.run` thread pool,
  `SCRAPER_WORKERS`, default 6); Playwright's sync API is fine one-per-thread.

### Detail-page fetches inside a single scraper

Enrichment (fetching each event's detail page for a real description) is
I/O-bound and safe to fan out. Two patterns:

- **`ThreadPoolExecutor(5)` for friendly sources.** SFPL: 1132 detail pages in
  ~2.5 min with 5 workers vs. ~19 min sequential. Reference: `sfpl.py` Phase 2.
- **Serial + fresh context per URL for anti-bot sources.** SFJAZZ (Cloudflare
  on a hot local IP) 403s under concurrency; only serial fetches with a fresh
  browser context per URL succeed reliably. Reference: `sfjazz.py` Phase 2 via
  `browser_session().fresh_context()`.

**Bound the enrichment window.** When a source's calendar goes months out but
detail-page enrichment is thousands of fetches, cap it to a near-term horizon
(`DESCRIPTION_WINDOW_DAYS = 60` in `sfjazz.py`) so a scrape finishes in
minutes, not hours. The un-enriched tail keeps the listing-page metadata as
description.

### Progress logging

Silence during a multi-minute scrape is scary. Every scraper should log with a
`[<source>]` prefix and `flush=True`, at these points:

- Each listing page or month fetched (`[sfpl] page 12: 15 new events, 0.2s`).
- Phase 2 start with total count and worker count.
- Every N detail fetches during enrichment (`DETAIL_LOG_EVERY = 50` in `sfpl.py`)
  or per-URL for slow sources.
- Final summary (`done: 1132 events, 1095 with rich descriptions`).

## Testing (TDD)

- Write the failing test first against a **trimmed, real** fixture in
  `service/tests/fixtures/` (capture real HTML/JSON, keep a few representative
  items + a bit of noise to prove your selector isolates the right thing).
- Test the pure `parse*` functions offline; verify `scrape()` live once.
- Cover: one event per showing, correct date+time (and year inference), the
  URL, description extraction (incl. noise-stripping), and the empty/fallback
  case.
- Run `service/.venv/bin/python -m pytest` from `service/`.

## Running & building

See `README.md` for `docker compose` usage. In short: `docker compose run --rm
scraper` scrapes → saves to Postgres → writes `frontend/events.json`. The DB is
an **ephemeral working store** (safe to `docker compose down -v`); the committed
`frontend/events.json` is the durable artifact the site serves. When a scraper's
*output* changes (URLs, descriptions), wipe the DB and re-scrape so stale rows
don't linger (dedup skips existing rows, it doesn't update them).

## Commits

One commit per logical change; keep the (large, regenerated) `events.json` in
its own commit so code stays reviewable. A push to `main` touching `frontend/`
deploys the site via GitHub Pages.
