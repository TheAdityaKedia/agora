# Feature Spec: Frontend Search (Ranking + Fuzzy)

Status: Draft · Owner: Agora · Target: standalone frontend feature

## Goal

Let people find events by typing what they're looking for — a title fragment, a
description keyword, a venue, a source — with **typo tolerance** and **relevance
ranking**. Search is another filter facet: it composes with the existing source,
date, and location filters (AND across facets).

**Non-goals for this phase:**
- Highlight of matched substrings in the rendered card.
- URL-synced / shareable search links.
- Server-side index or precomputed search artifacts (see §5).
- Semantic / embedding-based search.
- Date-decay ranking ("prefer things happening sooner"). Deferred; add if pure
  relevance ranking feels wrong after real use.

## Design principles

- **Search is a filter facet, not a mode.** It composes with sources/dates/
  location filters via AND, and clears via the existing Reset button.
- **Empty query = calendar.** When the input is empty, the view is exactly what
  it is today: date-grouped, chronological. Ranking only kicks in with a query.
- **Client-side, in-memory.** The manifest (`events.json`) stays unchanged. No
  precomputed search artifacts. Index built on page load, discarded on unload.
- **Library over hand-rolled.** Use [MiniSearch] (~24KB min+gz), vendored, for
  BM25 ranking, prefix/fuzzy matching, and field weights. Don't reinvent the
  scoring wheel for 2431 events.

[MiniSearch]: https://github.com/lucaong/minisearch

---

## 1. UX

### 1.1 Layout

A new filter row placed **first** in `#filters` (above Sources), because search
is the primary discovery affordance:

```
Search  [ text input, full-row-width, placeholder="Search events…" ]
Sources [dropdown]
Location (pill, hidden when unset)
Dates   [Today] [This week] From __ To __
```

The input is a single `<input type="search">` with a live-updating value bound
to `searchQuery`. No submit button, no debounce (see §4).

### 1.2 Rendering under an active query

- **Empty query** → current behavior: date-grouped, chronological, `h2.date`
  headers.
- **Non-empty query** → **flat list**, no date headers, sorted by descending
  MiniSearch score. Each card still shows its date + time in the existing
  `.time` slot (which currently shows time only when a date header groups the
  day) — so the reader always knows *when* the top-of-list result is.
  - Implementation: when a search query is active, the row's `.time` element
    shows the *full date + time* (e.g. `Fri Oct 4 · 8:00 PM`) instead of just
    the time. Falls back to time-only in the grouped-by-day empty-query view.

### 1.3 Filter composition

All existing filters continue to apply. The MiniSearch index covers every
event, so it doesn't need to be rebuilt when Sources/Dates/Location change.
Order of operations per query change:

1. MiniSearch ranks every event by relevance to the query.
2. The ranked list is filtered through the (sources ∩ dates ∩ location)
   predicates.
3. The survivors render, in rank order.

This composes cleanly: searching "jazz" while Sources is set to only "SFJAZZ
Center" returns only SFJAZZ jazz events, ranked.

### 1.4 Empty results

Same message as today: `"No events match the current filters."` No special-cased
"no search results" text.

### 1.5 Reset

The existing Reset button clears the search input alongside all other filters.

---

## 2. Ranking & Fuzzy Matching

### 2.1 Library

**MiniSearch**, vendored into `frontend/vendor/minisearch-<version>.min.js`
(e.g. `minisearch-6.3.0.min.js` — actual version pinned during implementation).
The site is a static single-page app with no other runtime dependencies;
vendoring keeps it self-contained and eliminates a CDN failure mode. Version
in the filename makes upgrades an explicit, reviewable diff.

### 2.2 Index

Built once on page load, after `events.json` is fetched and parsed. Each event
becomes one document:

```
{
  id: <stable index into events[]>,
  title, description, location, sources_text  // sources.join(' ')
}
```

MiniSearch config:

- `fields`: `["title", "description", "location", "sources_text"]`
- `storeFields`: `["id"]` (we only need to reconstruct the event from the array)
- `searchOptions.boost`: `{title: 3, description: 1, location: 1, sources_text: 1}`
- `searchOptions.prefix`: `true` — matches "jaz" against "jazz".
- `searchOptions.fuzzy`: `0.2` — edit-distance / term-length. Tolerates ~1 typo
  on short terms, ~2 on long ones.
- `searchOptions.combineWith`: `"AND"` — every query token must match somewhere
  (across all fields).

Build cost benchmark expectation: <500ms for 2431 events (MiniSearch's own
benchmarks show ~10k docs/sec on modest hardware). Runs after the fetch
completes; hidden behind the existing "Loading…" state.

### 2.3 Result assembly

```
results = miniSearch.search(query, opts)   // [{id, score}, …] desc by score
events_by_id = index into original array
ranked = results.map(r => events_by_id[r.id]).filter(passes_other_filters)
```

Other filters (source/date/location) are applied **after** ranking, not before,
because MiniSearch ranks against the whole indexed set and it's cheaper to
filter a ranked list of ~100 hits than to re-index on every filter change.

### 2.4 Score threshold

MiniSearch returns weak matches by default. Set `searchOptions.filter` (or
post-filter) to drop results whose score is under a floor — start at **1.0**
(MiniSearch's raw scores land in ~0.5–20+ range for realistic queries) and tune
empirically. Prevents "jaz" fuzzy-matching random `j`s.

### 2.5 What ranking does NOT do (v1)

- No date-decay. A jazz show tonight and one in 3 months score identically on
  content. If this feels wrong once we're using it, add a mild recency boost
  (e.g. `score * exp(-days_out / 60)`).
- No phrase-detection beyond MiniSearch's own token positional handling.
- No per-source weighting (we don't prefer, say, SFJAZZ hits over Fillmore
  hits).

---

## 3. State & Data Flow

**New state** in the IIFE alongside existing filter state:

```js
let searchQuery = "";
let miniSearch = null;         // built after events load
let rankedIds = null;          // Set<int> of event ids matching current query,
                               //   or null when query is empty
let rankedOrder = null;        // ordered list of event ids by score, or null
```

**Load sequence:**

1. Fetch `events.json`.
2. Materialize `events` array (existing).
3. Build MiniSearch index over `events`. Assign each event an `id` == its array
   index.
4. Render.

**Query change (input event):**

1. `searchQuery = input.value.trim()`.
2. If empty → `rankedIds = null; rankedOrder = null;` render (calendar view).
3. Else → run `miniSearch.search(query, opts)`; store id set and order.
4. Call `render()`.

**Render change:**

The existing `render()` gets one branch:

- If `rankedOrder` is null → today's date-grouped rendering.
- Else → filter `rankedOrder` through the other filter predicates (source/date/
  location), map ids to events, and call a new `renderFlat(events)` that emits
  event cards with full date+time in the `.time` slot, no `h2.date` headers.

---

## 4. Performance

- **Index build:** one-time, <500ms for current dataset. Runs during
  `"Loading…"`.
- **Per-keystroke query:** ~5-30ms typical for MiniSearch on this scale. No
  debounce.
- **Memory:** MiniSearch index ~2-3x the size of concatenated searchable text
  (~2-3MB in memory). Fine for browsers.

Revisit if we ever pass ~20k events — at that point ship a pre-built index as a
sibling JSON file, loaded lazily.

---

## 5. Precompute location: why not server-side

The searchable text (title, description, location, sources) is already in
`events.json`. Persisting a MiniSearch index alongside it would:

- ~double `events.json`'s size (posting lists + term dictionary).
- Add a rebuild step to the scraper pipeline and pin the search config to the
  scraper version.
- Buy ~300–500ms of load-time work we don't need to buy.

Client-side wins clearly at this dataset size. Escalation point: when index
build cost stops fitting inside the existing spinner, switch to a shipped
index. Not now.

---

## 6. Testing

Manual verification (no unit tests — pure UI, small surface):

- Type `jazz` → SFJAZZ events + jazz-titled events, ranked, no date headers.
- Type `branford` → the Branford Marsalis event ranks first even though the
  title doesn't perfectly match (proves fuzzy + description match).
- Type `jaz` (partial) → same as `jazz` (proves prefix match).
- Type `jazzz` (typo) → still returns jazz results (proves fuzzy).
- Type `jazz mission` → intersection: jazz events in the Mission.
- Type gibberish (`xzqwerty`) → empty state message.
- Combine with a date preset (`Today` + `jazz`) → both apply.
- Combine with a Source filter (only "SFJAZZ") + `jazz` → only SFJAZZ jazz.
- Clear the query → falls back to full date-grouped calendar.
- Reset button clears search alongside every other filter.

---

## 7. Phasing

One shippable chunk:

1. Vendor MiniSearch into `frontend/vendor/`.
2. Add search filter row + input.
3. Build the index on load.
4. Wire the search-active render branch (flat, ranked, full date+time in
   `.time`).
5. Wire Reset.
6. Manual verify per §6.

No backend changes.

---

## 8. Open questions

- **Fuzzy tolerance level.** Start at `0.2`; adjust after real use.
- **Score threshold.** Start at `1.0`; adjust after real use.
- **Full date+time format.** The exact layout of the enriched `.time` in
  search-active view (`Fri Oct 4 · 8:00 PM`?) — final wording chosen during
  implementation.
