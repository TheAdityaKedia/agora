# Feature Spec: Event Tagging (AI Classification)

Status: **IMPLEMENTED — this draft is superseded; the code is the source of truth.**

> ⚠️ This is the original design draft. The shipped feature diverged from it in
> two big ways after experimentation:
> - **Two axes, not one.** Type became a shallow *format* tree; genre/subject
>   moved to a separate flat, multi-select **topic** vocabulary (poetry, jazz,
>   theater, …). See `service/data/taxonomy.v1.json` (`axes.type` + `axes.topic`).
> - **Model: Claude Haiku 4.5 via Bedrock**, not Nova Micro (Haiku was markedly
>   more accurate in a head-to-head; cost is a one-time ~$1 for the catalog).
> - Added **`source_profiles.json`** (per-venue prior) for thin descriptions.
> Live code: `service/taxonomy.py`, `classify.py`, `classifications.py`,
> `exporters/json_export.py`, and the frontend filters in `frontend/index.html`.
> The rest of this doc is kept for historical rationale.

## Goal

Let people browse the calendar by the *kind* of thing an event is, not just by
venue or date — "show me free author talks", "a bachata social", "jazz this
Friday". Each event is classified into a **hierarchical type taxonomy** plus a
**cost** facet by an LLM, cheaply and once per show, and the frontend gains a
tag filter.

Non-goals (for this phase): free-form/semantic search, per-genre music
sub-sub-typing beyond the taxonomy, audience facets (kids/family), automated
re-classification triggers.

## Design principles (from DEVELOPMENT.md)

- **AI at the edges** — deterministic parsing stays deterministic; the LLM only
  turns unstructured title/description text into structured tags.
- **Classify once, cache forever** — a steady-state scrape (no new shows) makes
  **zero** LLM calls.
- **Taxonomy is versioned data, not scattered strings** — one source of truth
  drives the prompt, validation, and the frontend filter.

---

## 1. Taxonomy

### Representation

A hierarchical tree (2–3 levels) stored as **versioned JSON files** with the
version in the filename, so published versions are immutable and old
classifications stay interpretable against the exact tree they were made under.

- Files: `service/data/taxonomy.v1.json`, `taxonomy.v2.json`, … (never mutate a
  published file; a change = a new file).
- Each file is self-describing: it contains its own `"version": N` (sanity-check
  against the filename).
- Current pointer: one constant in code — `CURRENT_TAXONOMY_VERSION = 1` — which
  resolves to `taxonomy.v{N}.json`. Explicit, so a bump is a one-line reviewable
  diff next to the new file.

### File shape

```json
{
  "version": 1,
  "tree": {
    "performance": {
      "label": "Performance",
      "children": {
        "music": {
          "label": "Music",
          "children": {
            "jazz": {"label": "Jazz"}, "rock": {"label": "Rock"},
            "pop-indie": {"label": "Pop / Indie"}, "hiphop-rnb": {"label": "Hip-hop / R&B"},
            "electronic": {"label": "Electronic"}, "folk-americana": {"label": "Folk / Americana"},
            "classical": {"label": "Classical"}, "other": {"label": "Other"}
          }
        },
        "theater": {
          "label": "Theater",
          "children": {
            "musical-theater": {"label": "Musical theater"}, "play": {"label": "Play (drama)"},
            "opera": {"label": "Opera"}, "solo-show": {"label": "Solo / one-person show"},
            "cabaret-variety": {"label": "Cabaret / variety"}, "comedy-farce": {"label": "Comedy / farce"},
            "experimental": {"label": "Experimental / devised"}, "other": {"label": "Other"}
          }
        },
        "dance": {"label": "Dance"},
        "comedy": {"label": "Comedy"}
      }
    },
    "screening": {"label": "Screening", "children": {
      "film": {"label": "Film"}, "documentary": {"label": "Documentary"},
      "repertory": {"label": "Repertory / classic"}, "series-festival": {"label": "Series / festival"},
      "other": {"label": "Other"}}},
    "talk": {"label": "Talk", "children": {
      "author-reading": {"label": "Author talk / Reading"}, "lecture": {"label": "Lecture"},
      "panel": {"label": "Panel / Discussion"}, "podcast": {"label": "Live podcast"},
      "conversation": {"label": "Conversation / Q&A"}}},
    "workshop": {"label": "Workshop", "children": {
      "art-craft": {"label": "Art / Craft"}, "writing": {"label": "Writing"},
      "dance-class": {"label": "Dance class"}, "music": {"label": "Music"},
      "cooking": {"label": "Cooking"}, "wellness": {"label": "Wellness / Movement"}}},
    "exhibition": {"label": "Exhibition", "children": {
      "visual-art": {"label": "Visual art"}, "photography": {"label": "Photography"},
      "installation": {"label": "Installation / New media"},
      "design-architecture": {"label": "Design / Architecture"},
      "history-cultural": {"label": "History / Cultural"}}},
    "social": {"label": "Social", "children": {
      "dance-social": {"label": "Dance social", "children": {
        "zouk": {"label": "Zouk"}, "bachata": {"label": "Bachata"}, "salsa": {"label": "Salsa"},
        "fusion": {"label": "Fusion"}, "tango": {"label": "Tango"}, "swing": {"label": "Swing"},
        "other": {"label": "Other"}}},
      "party-club": {"label": "Party / Club night"}, "gala-fundraiser": {"label": "Gala / Fundraiser"},
      "mixer-meetup": {"label": "Mixer / Meetup"}, "food-drink": {"label": "Food & drink"}}}
  }
}
```

Depth is intentionally uneven: `Performance/Music`, `Performance/Theater`, and
`Social/Dance social` go three levels; everything else stops at two.

### Loader (`service/taxonomy.py`)

Single module, the only place that reads the JSON:
- `CURRENT_TAXONOMY_VERSION = 1`
- `load_taxonomy(version=CURRENT) -> dict` — reads `taxonomy.v{N}.json`.
- `valid_paths(version) -> set[tuple[str,...]]` — every valid path, **including
  partial paths** (a path may stop at any level: `("performance",)`,
  `("performance","music")`, `("performance","music","jazz")` are all valid).
- `validate_path(path, version) -> tuple|None` — returns the longest valid
  prefix of `path`, or `None` if even the top level is unknown. Used to coerce
  slightly-off LLM output (e.g. an unknown leaf falls back to its valid parent).
- `render_for_prompt(version) -> str` — the indented tree text injected into the
  classifier prompt.

---

## 2. Data model — the cache is a committed JSON file

Tags live in a **committed cache file**, `service/data/classifications.json`,
**not** in Postgres. Rationale: the Postgres DB is the *ephemeral local working
store* (routinely wiped with `docker compose down -v`, tied to one machine's
docker volume, empty in CI). A cache that costs Bedrock money to fill must
outlive DB wipes and be portable — so we store it durably in git, exactly like
`frontend/events.json`. "Classify once, cache forever" then actually holds.

- **`Event` is unchanged** — no new columns, no per-performance tag writes. The
  cache is keyed per *show* (`title` + `source`); events are per *performance*;
  the exporter joins each event to its show's classification at export time. A
  reclassify updates **one** entry and every performance of that show follows.
- **No new DB table.** (Trade-off: not SQL-queryable, but ~200 shows makes that
  irrelevant, and durability + portability + reviewable git diffs win. SQLite
  would survive wipes too but is a binary blob — bad diffs.)

### File shape (`service/data/classifications.json`)

```json
{
  "model_default": "amazon.nova-micro-v1:0",
  "entries": {
    "SFJAZZ Center\u001fBranford Marsalis Quartet": {
      "title": "Branford Marsalis Quartet",
      "source": "SFJAZZ Center",
      "types": [["performance", "music", "jazz"]],
      "cost": "paid",
      "model": "amazon.nova-micro-v1:0",
      "taxonomy_version": 1,
      "classified_at": "2026-09-21T18:04:00+00:00"
    }
  }
}
```

- **Key**: `f"{source}\x1f{title}"` (US unit-separator joins the two fields
  unambiguously). Matching is exact on the event's `source` + `title`.
- Loaded/saved by a small `classifications.py` cache module (`load()`,
  `get(source, title)`, `put(entry)`, `save()`); pretty-printed with sorted
  keys so commits diff cleanly (same discipline as the stable manifest export).

---

## 3. Classifier

### Input (per show)

A show is a distinct `(title, source)`. The classifier is sent:
- **`title`** — always present; strongest single signal.
- **`source`** — the venue; a strong type prior (SFJAZZ→music, Berkeley Rep→
  theater, City Lights→literature/talk, YBCA→exhibition).
- **`description`** — the **richest** description among that show's events
  (longest non-empty), since per-performance descriptions can vary (e.g. A.C.T.
  performance notes). After the description-provenance work, most sources now
  carry a real synopsis here.

### Output (validated JSON)

```json
{ "types": [["performance","music","jazz"]], "cost": "paid" }
```
- `types`: 1–3 taxonomy **paths**, most-relevant first. Each path may be partial
  (stop at any level) when the model can't confidently go deeper. Validated with
  `validate_path`: an unknown leaf coerces to its valid parent; a path with an
  unknown top level is dropped. The model is instructed to always return **at
  least one** path — the closest top-level guess (e.g. `[["talk"]]`) when unsure
  rather than nothing. Empty `types` persists only if every returned path failed
  validation, and renders as "Untagged".
- `cost`: single value; `unknown` is the escape hatch when the text doesn't say.

### Prompt (sketch)

System: "You classify event listings into a fixed taxonomy. Return ONLY JSON
matching the schema. Use 1–3 type paths, most relevant first; go as deep as the
listing supports and stop early if unsure. `cost` is free/paid/unknown."

User: the rendered taxonomy (`render_for_prompt`) + the event's
`title`, `source`, `description` + the JSON schema + 2–3 few-shot examples
(a jazz concert, a free author talk, a bachata social).

### Model & integration

- **Model: Amazon Nova Micro** (`amazon.nova-micro-v1:0`) via Bedrock — cheapest
  sensible option, text-only, Amazon-owned; a closed-set classify is exactly its
  strength. **Verify id/region/pricing before building.** Fallback: Claude
  Haiku if accuracy disappoints — the classifier interface is model-agnostic.
- **Integration**: `boto3` `bedrock-runtime` **Converse API**; parse the JSON
  from the reply and validate. New dep `boto3` in `requirements.txt`; AWS creds
  via the standard chain (env/role). A `classify_show(title, source, description)
  -> Classification` function isolates the LLM call so it's mockable in tests.
- Cost: fractions of a cent per show; the full ~200-show catalog classifies for
  well under a penny, and only on cache misses.

---

## 4. Pipeline integration

New step in `main.run()`, **after** the scrape/save loop and **before**
`export_json`:

```
scrape (concurrent) → save_events → classify_new_shows() → export_json
```

`classify_new_shows()`:
1. Query distinct `(title, source)` from `events` (upcoming horizon).
2. Load `classifications.json`; look up each show by `(source, title)`. **Skip**
   if present and `taxonomy_version == CURRENT` and model unchanged (cache hit →
   0 calls).
3. For misses: pick the richest description among that show's events, call
   `classify_show`, validate, `put()` the entry.
4. `save()` the cache file once at the end; print a summary
   (`[classify] N classified, M cached`).

The refreshed `classifications.json` is committed alongside `events.json` (both
are durable artifacts of a scrape run).

Steady state (no new shows) → zero LLM calls, matching the incremental grain of
the rest of the pipeline. Isolated as its own function so it can move to a
Celery task later (like `scrape_and_save`).

## 5. Reclassify (CLI)

Manual command (`python -m classify` from `service/`, or a `classify.py`
entrypoint):
- default (stale/missing): re-run shows where `taxonomy_version < CURRENT` or
  `model` changed, plus any missing.
- `--all`: re-run every distinct show from scratch.
- `--sources SUBSTR …`: limit to matching sources (mirrors `main.py`).

Because the cache is keyed per show, reclassify iterates *shows*, not events —
cheap and idempotent. It rewrites the matching entries in
`classifications.json` in place. Typical flow after a taxonomy change: add
`taxonomy.v2.json`, bump `CURRENT_TAXONOMY_VERSION`, run `python -m classify`,
commit the updated `classifications.json`.

## 6. Exporter + frontend

- `exporters/json_export.py`: load `classifications.json` and, for each event,
  **join** to its entry on `(source, title)` and emit `types` (list of paths)
  and `cost` into the manifest event object (omit/"Untagged" when no entry).
  Also emit the current taxonomy tree
  once at the top of the manifest (`"taxonomy": {...}`) so the static frontend
  builds its filter UI from the same source of truth (no separate fetch).
- `frontend/index.html`: a tag filter built from the manifest's `taxonomy` —
  top-level chips that expand to subcategories; multi-select; composes with the
  existing source/date/location filters (AND across facets, OR within). A `cost`
  toggle (free/paid). Events with empty `types` group under "Untagged".

### Shareable filtered links

Filter state lives in the **URL** so a filtered view is shareable — e.g. send a
friend "all dance events" as a link, and they open the page already filtered,
no manual steps.

- **Encode active filters in the URL query string**, on the existing static
  page (GitHub Pages serves the same `index.html` regardless of query, so no
  routing needed):
  `?type=performance.dance,social.dance-social&cost=free&from=2026-10-01`
  - `type`: comma-separated **dot-joined paths** (`performance.dance`); a
    partial path like `performance` matches that whole branch (all Performance).
  - reuse the same param scheme for the existing `source` / `from` / `to` /
    `location` filters, so *any* view is shareable, not just tags.
- **On load**: parse the query string → apply those filters before first render,
  so the shared link shows the filtered list immediately.
- **On any filter change**: rewrite the URL with `history.replaceState` (no
  reload, no history spam) so the address bar always reflects the current view —
  copy-link then shares exactly what's on screen.
- **"Copy link" affordance**: a small share button that copies the current URL,
  so users don't have to know the address bar carries state.
- **Robustness**: unknown/removed taxonomy paths in an old link are ignored
  (fall back to showing everything for that facet) rather than erroring — links
  shared before a taxonomy change still open gracefully.

This is pure front-end (no backend/manifest change beyond the `types`/`taxonomy`
already added above); it ships in Phase B with the filter UI.

## 7. Testing

- `taxonomy.py`: load current file; `valid_paths` includes partial paths;
  `validate_path` coerces unknown leaf → parent, drops unknown top level.
- `classify_show`: mock the Bedrock client; assert prompt contains the taxonomy
  and event fields; assert JSON parse + validation (valid, partial, garbage,
  off-taxonomy → coerced).
- `classifications.py` cache: `put`/`get`/`save` round-trip; key is
  `source\x1ftitle`; file is stably sorted (byte-identical re-save).
- cache/`classify_new_shows`: with a fake classifier, a second run makes zero
  calls (cache hit); a taxonomy bump makes calls again (stale).
- exporter: event joins to its cache entry; manifest carries `types`, `cost`,
  and `taxonomy`.
- frontend: URL filter round-trip — a query string applies filters on load; a
  filter change updates the URL; an unknown taxonomy path in a link is ignored
  gracefully (verified in a headless browser).
- No live Bedrock calls in tests.

## 8. Phasing

- **A (backend)**: `taxonomy.v1.json` + loader, `classifications.json` + cache
  module, `classify_show` + Bedrock, `classify_new_shows` in `run()`,
  `reclassify` CLI, exporter join + taxonomy in manifest. Ships tags into
  `events.json`.
- **B (frontend)**: the tag/cost filter UI **+ shareable filtered links**
  (filter state encoded in the URL, applied on load, "Copy link" button).

Do A first and verify tags land correctly in the manifest before touching the UI.

## 9. Open questions / to verify

- Nova Micro exact model id, region availability, and current pricing (verify
  against Bedrock before building; keep the Haiku fallback path).
- AWS creds/region for local `docker compose` runs (env vars into the scraper
  service) and for CI, if classification runs there.
- Whether empty `types` ("untagged") should ever persist, or the model must
  always return at least one top-level path (leaning: always ≥1).
- `(title, source)` matching: exact today; revisit light normalization
  (whitespace/case) only if real duplicates appear.
