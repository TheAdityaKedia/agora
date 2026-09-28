# Future Features

Planned work that isn't built yet. Each entry links to its spec in
`feature-specs/`; an entry without one needs a spec before code (see
`CONTRIBUTING.md`). When a feature ships, delete its entry and its spec — the
code is then the source of truth. New *sources* go in `future-sources.md`, not
here.

- **Adaptive / self-healing scrapers** — detect when a source's markup or
  endpoint changes and adapt instead of silently returning `[]`.
  Spec: [`feature-specs/adaptive-scrapers.md`](feature-specs/adaptive-scrapers.md)
  (early design; open decisions to settle first).
- **Frontend payload scaling** — the browser downloads all of `events.json` and
  builds the search index on load; past ~5k events ship a prebuilt index and/or
  paginate / lazy-load the manifest. Spec: not written.
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
- **`reclassify` CLI** — force re-tagging of stale or all shows (today only a
  taxonomy version bump or deleting the cache does it). Spec: not written.
- **Email / flyer ingestion** — forward an email or flyer screenshot to a
  monitored inbox; parse it (LLM for unstructured images) and add the event.
  Also the path for login-gated sources. Spec: not written.
- **Fuzzy / semantic dedup** — a third dedup layer beyond `(url, start_time)`
  and `(title, start_time)`, needed once OCR'd flyer titles arrive (pairs with
  email/flyer ingestion). Spec: not written.
