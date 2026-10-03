# Future Features

Planned work that isn't built yet. Each entry links to its spec in
`feature-specs/`; an entry without one needs a spec before code (see
`CONTRIBUTING.md`). When a feature ships, delete its entry and its spec — the
code is then the source of truth. New *sources* go in `future-sources.md`, not
here.

- **Venues and the Area filter** — a committed venue list resolved from
  location strings (OpenStreetMap + evidence rules + a review queue), then a
  region filter, venue names and links, venue-aware dedup, a map view and
  an SF neighbourhood filter. Phases 1–3 have shipped. Phase 4 (map +
  neighbourhoods) is built on the `venues-phase4-map` PR; it goes live
  once that merges and the next CI export adds coordinates to the manifest.
  Left after that:
  - **"Near me"** — deferred until the owner wants a location-permission
    prompt (browser geolocation, client-side only).
  - **Venue pages.**
  Spec: [`feature-specs/venues.md`](feature-specs/venues.md).
- **Adaptive / self-healing scrapers** — detect when a source's markup or
  endpoint changes and adapt instead of silently returning `[]`.
  Spec: [`feature-specs/adaptive-scrapers.md`](feature-specs/adaptive-scrapers.md)
  (early design; open decisions to settle first).
- **Collections (event canvases)** — save events for yourself, or share a
  collection so friends add events, vote 👍 by name, comment and pick a plan;
  duplicate / share a copy. Agora's first live backend (AWS Lambda +
  DynamoDB). Next: a one-tap ☆ Save on every event card.
  Spec: [`feature-specs/event-canvases.md`](feature-specs/event-canvases.md).
- **Frontend payload scaling** — the browser downloads all of `events.json` and
  indexes every description on load (16 s to the first row on a slow phone at
  5k events). Lean manifest + descriptions on demand.
  Spec: [`feature-specs/frontend-payload.md`](feature-specs/frontend-payload.md)
  (in progress).
- **Platform scrapers** — one scraper per shared ticketing backend (Veezi for
  indie cinemas, Eventive for film fests, VBO, Tixr) unlocks many venues at
  once, the way Eventbrite / Luma already do. Spec: not written.
- **Fix CI-blocked sources** — Green Apple, The Marsh, City Arts & Lectures, SF
  Playhouse fail from GitHub's datacenter IPs (stopgap:
  `scripts/scrape-to-neon.sh --blocked`). Details and options:
  `future-sources.md` → "Sources that need fixing". Spec: not written.
- **Drop detection in failure alerts** — also alert when a source's count falls
  sharply vs its current DB rows (would have caught SF Playhouse 375 → 4);
  today only hard failures and 0-event sources alert. Spec: not written.
- **Forced re-tagging** — re-tag *all* shows (or one source) on demand, e.g.
  after a prompt change without a taxonomy bump. The **Re-tag events**
  workflow (`retag.yml`) already catches up stale/missing tags without
  scraping; this would add a `--force` scope. Spec: not written.
- **Email submissions — follow-ups** (v1 shipped; see README → "Event
  submissions by email"):
  - *Probe agent for hard links*: venue homepages / JS pages / "tickets" pages
    that link to the real event fail today. A bounded, read-only agent
    (fetch / render / follow link / extract JSON-LD, ≤6 steps, no save tools,
    output through the same validator) — only for links the resolver failed on.
    Build it if `agora/failed` shows many such links.
  - *Event links from flyers*: use a printed info/tickets URL, and decode QR
    codes (OpenCV) when a flyer has one event.
  - *Don't reply about past events* on season flyers when other events from
    the same email were saved.
  - *Corrections/cancellations by email* (today: manual delete in Neon).
  - *End times*: extracted but dropped (events store a start time only).
  Spec: not written.
- **Fuzzy / semantic dedup — beyond v1** — v1 shipped (`service/dedup.py`):
  cross-source, same start time, normalized-title containment/overlap, and
  locations must agree. Not yet: doors-vs-show time differences, semantic
  (embedding/LLM) matching, and preferring the venue's own listing as the
  kept row (today the earlier source in `sources.txt` wins, so an aggregator's
  "Offsite: …" title can win over the venue's). Pre-v1 duplicates were merged
  on 2026-09-29 with `service/dedupe_existing.py` (29 rows).
  Spec: not written.
