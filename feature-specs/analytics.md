# Feature Spec: Product analytics

Status: Draft (2026-10-03) · Owner: Agora · Target: `analytics/` (collector + infra), `frontend/analytics.js`, `scripts/analytics_report.py`, `.github/workflows/analytics-digest.yml`

## Goal

Understand how the site performs as a product: how often it's visited and from
where, what people look for, what they click, and which parts of the catalog
(events, tags, sources, venues, areas) earn attention — so we know which
sources to invest in, which filters matter, and where the catalog has gaps.

**Non-goals (v1):**
- Tracking individuals across days, ad attribution, or anything needing a
  consent banner (no cookies, no persistent ids — §6).
- A public dashboard. Reports are private to the owner (the repo is public).
- Real-time views. Daily freshness is enough.
- Session replay / heatmaps.

## Design principles

- **First-party and small.** One collector endpoint in our own AWS account, in
  the same style as `canvas/` (Lambda Function URL + CDK). No third-party
  script on the page, nothing sold or shared.
- **Privacy by construction.** No cookies, no `localStorage` id, IPs never
  stored. Location comes from CloudFront's geo headers at country/region/city
  level; "unique visitors" use a daily-rotating salted hash (the Plausible
  approach), which can't link a person across days.
- **Event ids, not content.** Clicks record the event's manifest `id` and its
  facets (source, types, topics, area, venue) — enough to aggregate any way
  later without re-collecting.
- **Never break the site.** Beacons are fire-and-forget (`navigator.sendBeacon`,
  batched); a failing collector changes nothing the visitor sees.
- **Popularity is relative to supply.** "Most clicked tag" mostly measures how
  many events carry it. Every report shows clicks *per event listed* and, from
  v2, click-through rate (clicks ÷ impressions) next to raw counts.

## 1. Questions this answers

**Traffic and reach**
- Visits per day/week, unique visitors per day, pages per visit, trends.
- Where from: country → region → city (e.g. SF vs East Bay vs out of state).
- How they arrive: referrer domain (Google, Instagram, a newsletter, direct),
  UTM campaign, and **shared-link landings** (a first page with filters in the
  URL — someone sent them a filtered view).
- Device mix (mobile/desktop), browser family, language, viewport class.
- Time of day / day of week people browse.

**What people want**
- Most clicked **events**, **sources**, **types**, **topics**, **cost** (free vs
  paid), **areas**, **neighbourhoods**, **venues** — raw and per event listed.
- **Planning horizon:** how far ahead are clicked events (today / this week /
  later)?
- **Search:** top queries, queries with **zero results** (catalog gaps),
  queries followed by a click vs abandoned.
- **Filters:** which facets are used, common combinations, filter states that
  return nothing.
- **Map vs list:** how often the map opens; pin clicks vs list clicks.

**Engagement and outcomes**
- Engagement rate: share of visits with any interaction; share with an
  **outbound click** (the site's core success event — someone went to a
  venue/ticket page).
- Downstream intent: **Add to calendar** (Google vs .ics), **Share**,
  **Collections** (save, create, share, a shared collection opened).
- Position effects: rank of the clicked event in the list (are people only
  clicking the first screen?).

**Product health (v2)**
- Load performance from real visitors: time to first rendered row, manifest
  download size/time (feeds `frontend-payload.md`).
- JavaScript errors in the wild.
- Data freshness vs engagement (does traffic drop when a scrape fails?).

**Decisions it should drive**
- **Source value:** clicks and CTR per source — which scrapers to fix first,
  which candidate sources (`future-sources.md`) resemble high performers.
- **Taxonomy:** tags nobody filters or clicks; topics people search for that
  have no tag.
- **Coverage:** high-demand areas/types with few events → onboarding priorities.

## 2. What's collected

Every beacon carries: `t` (event type), `ts` (client time), `page`
(`index`/`canvas`), `vid` (per-page-load random id — groups a visit's events,
not persisted), and on `pageview` the context fields below. The collector adds
server-side fields (§3).

| Event | When | Fields |
|---|---|---|
| `pageview` | page load | path, referrer **domain** only, `utm_*`, landing filters present (bool + which facets), device class, viewport bucket, language |
| `search` | search settles (debounced 1.5 s, on blur, or on submit) | normalized query (lowercased, trimmed, ≤80 chars), result count |
| `filter` | a facet changes | facet (`type`/`topic`/`sources`/`area`/`hood`/`dates`/`venue`/`loc`), selected values, result count |
| `event_click` | outbound click on an event | event `id`, source(s), types, topics, cost, area, hood, venue id, days ahead, list rank, view (`list`/`map`), search active (bool), filters active (facets) |
| `calendar_add` | Google Calendar / .ics chosen | event `id`, kind |
| `share` | share / copy-link used | kind (`native`/`copy`), filters present |
| `map_open` / `map_pin` | map opened / pin clicked | — / event `id` |
| `collection` | save, create, share, shared collection opened | action, collection id hash (not the id) |
| `impression` (v2) | event rows that were on screen ≥1 s, batched | event `id`s, view |
| `perf` (v2) | once per load | ms to first row, manifest bytes + ms, event count |
| `error` (v2) | `window.onerror` / `unhandledrejection` | message (≤200 chars), file:line — deduped per load |

**Not collected:** IP addresses, full URLs of other sites, user agents beyond
family/version class, anything typed except the search box, anything from
collection contents (names, comments).

## 3. Architecture

```
frontend/analytics.js ──sendBeacon (batched JSON)──▶ CloudFront (analytics-in.…)
                                                        │ adds CloudFront-Viewer-Country/Region/City headers
                                                        ▼
                                                 Lambda Function URL  (analytics/collector.py)
                                                   validate · drop bots · hash visitor · enrich geo
                                                        │ PutRecordBatch
                                                        ▼
                                          Firehose → S3 (private, partitioned by day, JSON/Parquet)
                                                        │
                        Athena (Glue table) ◀───────────┘
                           │
        scripts/analytics_report.py (owner, local, `agora` profile)
        .github/workflows/analytics-digest.yml (weekly email via the agora Gmail SMTP)
```

- **Collector** (`analytics/collector.py`): accepts `POST` with ≤50 events,
  ≤16 KB; CORS only for the Pages origin (and localhost in dev); rejects
  unknown event types/fields; drops obvious bots (UA list, headless markers,
  our own Playwright checks via a `?noanalytics` flag); computes
  `visitor = sha256(daily_salt + ip + ua_family)` — the salt is random per
  day, held only in memory/SSM for that day and then discarded, so hashes
  can't be reversed or linked across days; reads geo from CloudFront headers;
  writes to Firehose. Returns 204 always (no information to scrapers).
- **Why CloudFront in front:** viewer geo headers (country/region/city) without
  storing or looking up IPs, plus caching-free edge TLS and shield. Free tier.
- **Storage:** Firehose batches to S3 (`s3://agora-analytics-<acct>/events/dt=YYYY-MM-DD/`),
  converted to Parquet; lifecycle: raw events kept **13 months**, then
  deleted. Aggregates (below) kept indefinitely.
- **Queries:** a Glue table over the bucket; Athena queries are
  pay-per-scan (pennies at our volume). Saved queries in
  `analytics/queries/*.sql` (one per report section).
- **Reports:**
  - `scripts/analytics_report.py [--days 7]` — prints the digest locally.
  - Weekly **digest email** (GitHub Action, Mondays): traffic, top N for each
    facet (raw + per event listed), top/zero-result searches, engagement
    funnel, week-over-week deltas. Sent from the agora Gmail account to the
    owner. Nothing is posted to issues, PRs or logs (the repo is public).
  - Daily rollup table (`daily_*`, written by the digest job) so long-range
    trends don't rescan raw data.
- **Infra:** CDK app in `analytics/infra/` (stacks `AgoraAnalyticsDev` /
  `Prod`), deployed like `canvas/` by a workflow; AWS Budget alert covers it.

**Alternatives considered**

| Option | Verdict |
|---|---|
| Google Analytics 4 | Rejected: third-party script, cookies + consent banner, data leaves our hands, heavy. |
| Plausible / Fathom (hosted) | Good privacy model, but ~$9–14/mo and custom-event properties are limited; facet-level analysis (per tag/venue) is awkward. |
| GoatCounter / Cloudflare Web Analytics | Free, but pageviews-first; no rich custom events. Fine as a stopgap for traffic only. |
| Write events to Neon | Rejected: every pageview would keep the free-tier compute awake and compete with scrapes; wrong tool for append-only events. |
| DynamoDB (like canvases) | Rejected for raw events: no ad-hoc aggregation; Athena over S3 is cheaper and queryable. |

## 4. Frontend (`frontend/analytics.js`)

- Tiny module (≤3 KB), loaded `defer`; exposes `track(type, fields)`. Queues
  events and flushes every 5 s, at 20 events, and on `visibilitychange`/
  `pagehide` via `navigator.sendBeacon` (falls back to `fetch(..., {keepalive})`).
- Instrumentation points in `index.html`: the event title link and "Get
  tickets"-style links (`event_click`), the calendar menu, share button,
  filter handlers (reuse the URL-state code in `writeStateToUrl` — one hook
  sees every facet change), search input, map open/pin, collections client.
- `event_click` uses `mousedown`/`auxclick` + `click` so middle-click and
  new-tab opens count; navigation is never delayed.
- **Off switches:** `?noanalytics` (sticky in `sessionStorage` for that tab),
  `navigator.doNotTrack === "1"` / Global Privacy Control respected, and a
  build-time `ANALYTICS_ENABLED` constant. Playwright verification runs set
  `?noanalytics`.
- Footer line: "Agora counts visits anonymously — no cookies." linking a short
  privacy note (`frontend/privacy.html`).

## 5. Reports (v1 digest contents)

1. **Traffic:** visits, unique visitors (daily-hash sum), pages/visit, by day;
   top countries/regions/cities; referrers; devices; shared-link landings.
2. **Engagement funnel:** visits → any interaction → outbound click →
   calendar/share/save.
3. **Top events** (clicks) with source, date and days-ahead.
4. **Facets:** for sources, types, topics, cost, areas, hoods, venues — clicks,
   events listed, **clicks per event listed**; arrows for week-over-week.
5. **Search:** top queries, zero-result queries, queries with no click.
6. **Filters:** usage by facet, top combinations, empty-result states.
7. **Map:** opens, pin clicks.
8. **Collections:** saves, creates, shares, shared opens.

"Events listed" comes from the manifest snapshot for that week (the digest
reads the published `events.json` history, or the daily rollup records the
catalog counts).

## 6. Privacy

- No cookies or persistent identifiers → no consent banner (cookieless,
  aggregate analytics; the daily hash can't follow anyone across days).
- IPs used only in memory for the hash and dropped; geo at city level from
  CloudFront, never coordinates.
- Search queries are the most sensitive field: lowercased, length-capped,
  never joined to location below country level in reports, and queries seen
  from fewer than 3 visitors are bucketed as "(rare)" in the digest.
- Honors DNT/GPC; `?noanalytics` opt-out; raw data deleted after 13 months.
- Bucket private, block-public-access on, encrypted (SSE-S3); collector role
  can only `PutRecordBatch`; Athena results bucket private with 30-day expiry.
- Privacy note page states all of the above in plain language.

## 7. Cost (estimate)

At ~1,000 visits/day × ~15 events/visit ≈ 450k events/month (~150 MB):
Lambda + Function URL ≈ $0 (free tier), CloudFront ≈ $0 (free tier),
Firehose ≈ $0.01, S3 ≈ $0.01, Athena (weekly digest + ad-hoc) < $0.10.
**≈ $0–1/month.** Covered by the existing `agora-monthly` budget; the stack
gets its own budget line alarm too.

## 8. Phases

- **v1 — Collect + digest:** collector + CloudFront + Firehose/S3/Athena,
  `analytics.js` with `pageview`, `search`, `filter`, `event_click`,
  `calendar_add`, `share`, `map_*`, `collection`; privacy page; CLI report;
  weekly email.
- **v2 — Rates and health:** `impression` (sampled/batched) → CTR per event,
  tag, source, venue, rank; `perf` and `error`; daily rollups; alert (issue
  without data, just "collector failing") when the collector's error rate or
  volume drops to zero.
- **v3 — Dashboard:** a private, auth-gated page (or a static report in the
  private bucket behind a signed URL) with charts; cohort-ish metrics if ever
  needed (still no persistent ids — e.g. "landed via shared link → clicked").

## 9. Testing

- **Collector (pytest + moto):** schema validation, size limits, bot drops,
  CORS, geo header parsing, hash rotation (same visitor same day → same hash;
  next day → different), Firehose payload shape, always-204.
- **Infra:** CDK assertions (bucket private/encrypted, lifecycle 13 months,
  role least-privilege, CloudFront forwards only the geo headers).
- **Frontend (Playwright, headless):** each instrumented action produces the
  expected beacon (intercept the collector URL); `?noanalytics`, DNT and
  collector-down cases send nothing / break nothing; no measurable change to
  time-to-first-row.
- **Reports:** queries tested against a small fixture dataset in local Athena
  stand-in (DuckDB over the same Parquet) so SQL is checked in CI.

## 10. Open questions

1. **Digest delivery:** weekly email (proposed) vs also a daily one-liner?
2. **Search query retention:** keep raw (capped) queries 13 months, or only
   aggregate counts after 30 days?
3. **Domain:** the collector lives at a CloudFront URL; fine, or use a
   subdomain if Agora gets a custom domain?
4. **Returning visitors:** v1 deliberately can't measure them. Acceptable, or
   is an explicit, consented opt-in worth it later?
5. **Collections:** count from the canvas API's own logs instead of client
   beacons (more reliable), or both?

## Files touched (v1)

- `analytics/collector.py`, `analytics/infra/` (CDK), `analytics/queries/*.sql`,
  `analytics/tests/`
- `frontend/analytics.js`, `frontend/index.html` + `canvas-client.js`
  (instrumentation), `frontend/privacy.html`, footer note
- `scripts/analytics_report.py`, `.github/workflows/analytics-digest.yml`,
  `.github/workflows/deploy-analytics.yml`
- `README.md` (operations: opt-out, reports, retention), `future-features.md`
