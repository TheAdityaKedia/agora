# Frontend Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add typo-tolerant, ranked search to Agora's frontend as another filter facet that composes with the existing source/date/location filters.

**Architecture:** Vendor MiniSearch into `frontend/vendor/`, build an in-memory index on page load, and add a search input as the first filter row. Empty query preserves today's date-grouped calendar view; non-empty query renders a flat ranked list with each row showing full date + time. Other filters run after ranking.

**Tech Stack:** Vanilla HTML/JS/CSS in `frontend/index.html`, [MiniSearch] 6.x (vendored). No build step, no framework, no backend changes.

[MiniSearch]: https://github.com/lucaong/minisearch

**Spec:** `feature-specs/search.md`

## Global Constraints

- **Vendor, don't CDN.** MiniSearch ships at `frontend/vendor/minisearch-<version>.min.js`, referenced by a `<script>` tag in `index.html`. Version pinned in filename (e.g. `minisearch-6.3.0.min.js`) — see spec §2.1.
- **No changes to `events.json` or scraper.** Search is pure client-side; the manifest schema is untouched. See spec §5.
- **Search composes with existing filters via AND.** MiniSearch indexes all events; sources/dates/location filters apply after ranking. See spec §1.3, §2.3.
- **Empty query = calendar view untouched.** Date-grouped, chronological. Non-empty query = flat, rank-ordered, each row shows full date + time in `.time` slot. See spec §1.2.
- **Fixed MiniSearch config** (spec §2.2, §2.4): `fields = ["title","description","location","sources_text"]`, `boost = {title:3, description:1, location:1, sources_text:1}`, `prefix: true`, `fuzzy: 0.2`, `combineWith: "AND"`, score threshold `1.0`.
- **No automated tests for frontend.** The site has no test harness; every task ends with a **manual browser verification** step. This is a deliberate scope choice — spec §6 lists the manual checks that stand in for a test suite.
- **Standard commit style:** one commit per task, imperative subject line, no attribution footer in the body. Look at `git log --oneline` for the tone (see e.g. `SFPL: pull rich descriptions from detail pages` on `main`).

---

### Task 1: Vendor MiniSearch

**Files:**
- Create: `frontend/vendor/minisearch-<version>.min.js` (verbatim from the MiniSearch release — do NOT edit its contents)
- Modify: `frontend/index.html` — add a `<script>` tag before the existing `<script>` block, so `window.MiniSearch` is defined before our IIFE runs.

**Interfaces:**
- Consumes: nothing.
- Produces: `window.MiniSearch` — a constructor exposed by the UMD bundle.

- [ ] **Step 1: Find the current stable MiniSearch UMD bundle**

Fetch the current stable release from a CDN and save it to the repo. Command:

```bash
cd /Users/kediaadi/workspace/LearningProjects/Agora
mkdir -p frontend/vendor
# Pin an exact version — do NOT use "latest".
VERSION="6.3.0"
curl -fL "https://cdn.jsdelivr.net/npm/minisearch@${VERSION}/dist/umd/index.min.js" \
  -o "frontend/vendor/minisearch-${VERSION}.min.js"
# Verify it downloaded a non-trivial file
wc -c "frontend/vendor/minisearch-${VERSION}.min.js"
```

Expected: a file ~25-40KB in size. If the CDN 404s (version yanked), try npm directly:

```bash
npm pack minisearch@${VERSION}
tar -xzf minisearch-${VERSION}.tgz --strip-components=1 package/dist/umd/index.min.js
mv index.min.js "frontend/vendor/minisearch-${VERSION}.min.js"
rm minisearch-${VERSION}.tgz
```

- [ ] **Step 2: Add the script tag to index.html**

Insert **immediately before** the existing `<script>` opening tag (around line 235 in the current file):

```html
<script src="./vendor/minisearch-6.3.0.min.js"></script>
```

Use the exact filename you downloaded in Step 1.

- [ ] **Step 3: Browser verification**

```bash
cd /Users/kediaadi/workspace/LearningProjects/Agora/frontend
python3 -m http.server 8765 > /tmp/agora.log 2>&1 &
sleep 1
open http://localhost:8765/
```

In the browser DevTools console, run:

```js
typeof MiniSearch   // expected: "function"
new MiniSearch({fields: ['title']})   // expected: an object, no error
```

Then kill the server:

```bash
pkill -f "http.server 8765" || true
```

- [ ] **Step 4: Commit**

```bash
git add frontend/vendor/minisearch-6.3.0.min.js frontend/index.html
git commit -m "Vendor MiniSearch for frontend search"
```

---

### Task 2: Add search filter row UI (input only, no wiring yet)

**Files:**
- Modify: `frontend/index.html` — add a new `.filter-row` inside `<section class="filters">`, placed **first** (above the existing Sources row). Add a CSS block for the new `.search-row` / input styling. Add a `const searchEl = document.getElementById("search-input")` line in the IIFE next to the other DOM references.

**Interfaces:**
- Consumes: MiniSearch script tag from Task 1 (already loaded).
- Produces:
  - DOM element `#search-input` — `<input type="search">`.
  - `const searchEl` in the IIFE, bound to that element.
  - No behavioral change yet — typing in the input has no effect; the DOM is inert.

- [ ] **Step 1: Add the CSS for the search row**

Inside the existing `<style>` block, after the `.filter-row { ... }` rule (around line 48), add:

```css
.filter-row.search-row {
  gap: 12px;
}
.filter-row.search-row input[type="search"] {
  flex: 1;
  padding: 6px 10px;
  border: 1px solid var(--border); border-radius: 6px;
  background: var(--input-bg); color: var(--fg);
  font: inherit; font-size: 14px;
  outline: none;
}
.filter-row.search-row input[type="search"]:focus {
  border-color: var(--accent);
}
```

- [ ] **Step 2: Add the search filter row HTML**

Inside `<section class="filters" id="filters" hidden>`, insert this row **immediately after** the opening `<section ...>` tag, so it appears above the existing "Sources" row (currently line 193):

```html
  <div class="filter-row search-row">
    <span class="filter-label">Search</span>
    <input type="search" id="search-input" placeholder="Search events…" aria-label="Search events" autocomplete="off">
  </div>
```

- [ ] **Step 3: Bind the DOM reference in JS**

Inside the async IIFE, alongside the other `const XxxEl = document.getElementById(...)` lines (around line 246), add:

```js
  const searchEl = document.getElementById("search-input");
```

- [ ] **Step 4: Browser verification**

```bash
cd /Users/kediaadi/workspace/LearningProjects/Agora/frontend
python3 -m http.server 8765 > /tmp/agora.log 2>&1 &
sleep 1
open http://localhost:8765/
```

Visually confirm:
- A `Search` filter row appears at the top of the filters section, above `Sources`.
- The input is full-row-width and shows placeholder `Search events…`.
- Typing has no effect on the event list yet (that's Task 4).
- Existing filters still work: click `Today`, click a source dropdown item, click a location — all should still filter as before.

Kill the server:
```bash
pkill -f "http.server 8765" || true
```

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html
git commit -m "Add search filter row (UI only)"
```

---

### Task 3: Build MiniSearch index at page load

**Files:**
- Modify: `frontend/index.html` — inside the async IIFE, after the `events.map(...)` line that materializes `_start` (~line 267 in the current file), assign each event an `id` and pre-compute a `sources_text` field, then build the MiniSearch index.

**Interfaces:**
- Consumes: `window.MiniSearch` (Task 1), `events` array (existing).
- Produces:
  - `events[i].id === i` — a stable numeric id used as the MiniSearch document id.
  - `let miniSearch = null; ... miniSearch = new MiniSearch(...)` — the built index. Kept in the IIFE closure.
  - Index build happens once on load, hidden behind the existing "Loading…" state.

- [ ] **Step 1: Assign ids and build the index**

Locate the line that materializes events (currently line 267):

```js
  const events = (data.events || []).map(e => ({...e, _start: new Date(e.start_time)}));
```

Change it to also assign `id` and `sources_text`, and **immediately after** it, build the index:

```js
  const events = (data.events || []).map((e, i) => ({
    ...e,
    _start: new Date(e.start_time),
    id: i,
    sources_text: (e.sources || []).join(" "),
  }));

  // Build the search index once. See feature-specs/search.md §2.2 for the
  // config rationale.
  const miniSearch = new MiniSearch({
    fields: ["title", "description", "location", "sources_text"],
    storeFields: ["id"],
    searchOptions: {
      boost: {title: 3, description: 1, location: 1, sources_text: 1},
      prefix: true,
      fuzzy: 0.2,
      combineWith: "AND",
    },
  });
  miniSearch.addAll(events);
```

- [ ] **Step 2: Browser verification**

```bash
cd /Users/kediaadi/workspace/LearningProjects/Agora/frontend
python3 -m http.server 8765 > /tmp/agora.log 2>&1 &
sleep 1
open http://localhost:8765/
```

In DevTools console, on the loaded page:

- The list renders exactly as before (index build is invisible to the user).
- No errors in the console.
- Time-to-render should still feel snappy — MiniSearch on 2431 events builds in <500ms.

The `miniSearch` variable is inside a closure so you can't query it directly. To sanity-check that indexing worked, temporarily add `window._ms = miniSearch;` right after `addAll`, reload, then in console:

```js
window._ms.search("jazz").slice(0, 3)   // expected: an array of {id, score, terms, ...} matches
window._ms.search("jaz").slice(0, 3)    // expected: also matches (prefix)
window._ms.search("jazzz").slice(0, 3)  // expected: still matches (fuzzy)
```

Remove the `window._ms` line before committing.

Kill the server:
```bash
pkill -f "http.server 8765" || true
```

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "Build MiniSearch index on page load"
```

---

### Task 4: Wire search — flat ranked view + filter composition

**Files:**
- Modify: `frontend/index.html` — add `searchQuery` / `rankedIds` / `rankedOrder` state, wire the input's `"input"` event, branch `render()` on `rankedOrder`, and add a new `renderFlat()` helper that produces event cards with full date + time.

**Interfaces:**
- Consumes: `miniSearch` (Task 3), `searchEl` (Task 2), existing filter state (`selected`, `dateFrom`, `dateTo`, `locationFilter`), existing helpers (`escapeHtml`, `renderDescription`).
- Produces:
  - `let searchQuery = ""`, `let rankedOrder = null` — closure state driving the search-active branch.
  - Input `"input"` event handler (`updateSearch`) that updates these and calls `render()`.
  - `render()` branches: when `rankedOrder` is set, calls `renderFlat(events)` after filtering by other facets; otherwise calls the existing `renderList(events)` (grouped).
  - `renderFlat(events)` — returns HTML with each row's `.time` slot showing `Fri Oct 4 · 8:00 PM`, no `h2.date` headers.
  - Class `.searching` on `<body>` while a query is active (drives CSS width tweak).

- [ ] **Step 1: Add state and input listener**

Immediately after the existing `let locationFilter = null;` line (~line 279 in current file), add:

```js
  let searchQuery = "";
  let rankedOrder = null;  // number[] | null — event ids in rank order, null when no search is active

  const SEARCH_SCORE_THRESHOLD = 1.0;  // Drop weak matches — spec §2.4

  function updateSearch() {
    const q = searchEl.value.trim();
    searchQuery = q;
    if (!q) {
      rankedOrder = null;
    } else {
      const results = miniSearch.search(q).filter(r => r.score >= SEARCH_SCORE_THRESHOLD);
      rankedOrder = results.map(r => r.id);
    }
    render();
  }
  searchEl.addEventListener("input", updateSearch);
```

- [ ] **Step 2: Branch `render()` on search-active state**

Locate the `function render() { ... }` block (~line 449). Replace it with:

```js
  function render() {
    const otherFiltersPass = (ev) => {
      const evSources = ev.sources || [];
      if (!evSources.some(s => selected.has(s))) return false;
      if (dateFrom && ev._start < dateFrom) return false;
      if (dateTo && ev._start > dateTo) return false;
      if (locationFilter && ev.location !== locationFilter) return false;
      return true;
    };

    let filtered;
    let flat = false;
    if (rankedOrder) {
      // Search-active branch: apply other filters AFTER ranking (spec §1.3),
      // preserving MiniSearch's descending-score order.
      filtered = rankedOrder.map(id => events[id]).filter(otherFiltersPass);
      flat = true;
    } else {
      filtered = events.filter(otherFiltersPass);
    }

    const isFiltered = filtered.length !== events.length;
    meta.textContent = (isFiltered ? filtered.length + " of " + events.length : events.length)
      + " upcoming events · updated " + generatedAt.toLocaleString();

    if (filtered.length === 0) {
      list.innerHTML = '<div class="empty">No events match the current filters.</div>';
      return;
    }
    list.innerHTML = flat ? renderFlat(filtered) : renderList(filtered);
  }
```

- [ ] **Step 3: Add `renderFlat` helper**

Immediately after the existing `renderList` function (~line 514, before its closing `})();`), add:

```js
  // Flat ranked rendering — used when a search query is active. No date
  // headers; each row's `.time` shows the full date + time so the reader
  // always knows *when* the top-of-list result is.
  const flatDateFmt = new Intl.DateTimeFormat(undefined, {
    weekday: "short", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit",
  });
  function renderFlat(events) {
    const parts = [];
    for (const ev of events) {
      const when = escapeHtml(flatDateFmt.format(ev._start));
      const title = escapeHtml(ev.title);
      const titleHtml = ev.url
        ? '<a href="' + escapeHtml(ev.url) + '" target="_blank" rel="noopener">' + title + "</a>"
        : title;
      const loc = ev.location
        ? '<div class="location"><button type="button" class="location-btn" data-location="'
            + escapeHtml(ev.location) + '" title="Filter by this location">'
            + escapeHtml(ev.location) + '</button></div>'
        : "";
      const src = '<div class="source">' + escapeHtml((ev.sources || []).join(" · ")) + "</div>";
      const desc = renderDescription(ev.description);
      const thumb = ev.image_url
        ? '<div class="thumb"><img loading="lazy" decoding="async" alt="" src="'
            + escapeHtml(ev.image_url) + '"></div>'
        : "";
      parts.push(
        '<div class="event">' +
          '<div class="time">' + when + "</div>" +
          thumb +
          '<div class="details">' +
            '<div class="title">' + titleHtml + "</div>" +
            loc + src + desc +
          "</div>" +
        "</div>"
      );
    }
    return parts.join("");
  }
```

- [ ] **Step 4: Widen the `.time` column for the flat view**

The current `.time` CSS is `flex: 0 0 72px` — too narrow for `Fri Oct 4 · 8:00 PM`. Add this rule below the existing `.time { ... }` rule in `<style>`:

```css
.event .time { line-height: 1.3; }
/* Search-active flat rows put a full date + time in .time — widen it. */
body.searching .time { flex: 0 0 140px; }
```

Then, in `updateSearch()` (Step 1), toggle a class on `<body>` so the CSS applies only during an active search. Update the function to:

```js
  function updateSearch() {
    const q = searchEl.value.trim();
    searchQuery = q;
    if (!q) {
      rankedOrder = null;
      document.body.classList.remove("searching");
    } else {
      const results = miniSearch.search(q).filter(r => r.score >= SEARCH_SCORE_THRESHOLD);
      rankedOrder = results.map(r => r.id);
      document.body.classList.add("searching");
    }
    render();
  }
```

- [ ] **Step 5: Browser verification (the big one — walk spec §6)**

```bash
cd /Users/kediaadi/workspace/LearningProjects/Agora/frontend
python3 -m http.server 8765 > /tmp/agora.log 2>&1 &
sleep 1
open http://localhost:8765/
```

Walk each of these, all should hold:

- Type `jazz` → results are a flat list (no date headers), SFJAZZ events + jazz-titled shows appear at the top. The `.time` column now shows something like `Fri Oct 4 · 8:00 PM`.
- Type `branford` → the Branford Marsalis event ranks in the top few.
- Type `jaz` (short) → same as `jazz` (prefix match).
- Type `jazzz` (typo) → still returns jazz results (fuzzy).
- Type `jazz mission` → intersection with Mission-district venues.
- Type `xzqwerty` → "No events match the current filters."
- Combine `Today` preset + `jazz` → jazz events happening today (or empty).
- Combine Source filter (deselect all but "SFJAZZ Center") + `jazz` → only SFJAZZ jazz events, ranked.
- Delete the query → the calendar view returns, date headers reappear, `.time` is time-only again.

Kill the server:
```bash
pkill -f "http.server 8765" || true
```

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html
git commit -m "Wire search input to MiniSearch (flat ranked view)"
```

---

### Task 5: Wire Reset button + close-out verification

**Files:**
- Modify: `frontend/index.html` — extend the existing Reset button handler to clear the search input and re-run `updateSearch()`.

**Interfaces:**
- Consumes: `searchEl` (Task 2), `updateSearch()` (Task 4).
- Produces: no new symbols; extends behavior of the existing `#reset` click handler.

- [ ] **Step 1: Extend the Reset handler**

Locate the existing `resetEl.addEventListener("click", ...)` block (~line 433). Change it from:

```js
  resetEl.addEventListener("click", () => {
    for (const s of allSources) { selected.add(s); checkboxes.get(s).checked = true; }
    updateSourceSummary();
    fromEl.value = ""; toEl.value = ""; dateFrom = dateTo = null;
    updatePresetHighlight();
    setLocationFilter(null);
  });
```

to:

```js
  resetEl.addEventListener("click", () => {
    for (const s of allSources) { selected.add(s); checkboxes.get(s).checked = true; }
    updateSourceSummary();
    fromEl.value = ""; toEl.value = ""; dateFrom = dateTo = null;
    updatePresetHighlight();
    searchEl.value = "";
    updateSearch();          // clears searchQuery / rankedOrder, removes .searching, and re-renders
    setLocationFilter(null); // last, because it also calls render()
  });
```

- [ ] **Step 2: Browser verification**

```bash
cd /Users/kediaadi/workspace/LearningProjects/Agora/frontend
python3 -m http.server 8765 > /tmp/agora.log 2>&1 &
sleep 1
open http://localhost:8765/
```

- Type `jazz`, apply `This week`, click a location on a card. Click Reset → search input is empty, dates cleared, sources all selected, location pill gone, list back to the full calendar view.
- Type `jazz` alone. Click Reset → search input cleared, list is the full calendar.

Kill the server:
```bash
pkill -f "http.server 8765" || true
```

- [ ] **Step 3: Regenerate events.json (optional; only if the on-disk manifest is stale)**

Only needed if `frontend/events.json` hasn't been refreshed lately. Skip if the manifest already reflects recent scrapes.

```bash
cd /Users/kediaadi/workspace/LearningProjects/Agora
docker compose run --rm scraper
```

If run, commit the resulting `frontend/events.json` in its own commit per `CONTRIBUTING.md`.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html
git commit -m "Wire Reset button to clear search"
```

- [ ] **Step 5: Push to main (deploys to GitHub Pages)**

```bash
git push origin main
```

The site rebuilds via GitHub Pages in ~1 min. Reload the live URL and re-run the spec §6 checks against the deployed site.

---

## Spec coverage self-check

- **§1.1 Layout** — Task 2 (search row placed first).
- **§1.2 Rendering under active query** — Task 4 (`renderFlat`, `body.searching` class widens `.time`).
- **§1.3 Filter composition (rank first, filter after)** — Task 4 Step 2 (`rankedOrder.map(...).filter(otherFiltersPass)`).
- **§1.4 Empty results** — Task 4 Step 2 (reuses existing empty message).
- **§1.5 Reset** — Task 5.
- **§2.1 Vendored library** — Task 1.
- **§2.2 Index config** — Task 3 (fields, boost, prefix, fuzzy, combineWith).
- **§2.3 Result assembly / rank-then-filter** — Task 4 Step 2.
- **§2.4 Score threshold** — Task 4 Step 1 (`SEARCH_SCORE_THRESHOLD = 1.0`).
- **§3 State & data flow** — Task 4 (state additions, input listener, render branch).
- **§4 Performance** — implicit; no debounce, no rebuild on filter change.
- **§5 Precompute location (client-side only)** — no scraper/manifest changes.
- **§6 Manual verification** — Task 4 Step 5, Task 5 Step 2.
- **§7 Phasing (one shippable chunk)** — Task 5 Step 5 pushes to main.
