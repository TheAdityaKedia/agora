# Event lifecycle: stable ids, changes and cancellations

**Status: spec, not started (2026-10-05).** Covers two gaps recorded in
`future-features.md` → Collections: *event copies drift* and *event ids
aren't stable*. They are one project: you can only say "this event changed"
or "was cancelled" if "this event" keeps its identity from one scrape to the
next.

## The problem today

1. **Rows never change.** `main.save_events` matches a scraped event to an
   existing row (same URL + start, same title + start, or a fuzzy
   cross-source match) and then **skips** it if the source is already on the
   row. New titles, venues, descriptions or links never land. CLAUDE.md
   documents the workaround: delete a source's rows in Neon and re-scrape.
2. **Nothing notices an event disappear.** A show that is cancelled, moved
   or simply delisted stays in the database, and so on the main site, until
   its date passes. Scrapers that see an explicit "cancelled" flag
   (`alembic.py`, `facebook.py`, `momence.py`) drop the event, so even an
   explicit cancellation is invisible.
3. **Ids are random** (`uuid.uuid4()` per row). Deleting and re-scraping a
   source (the workaround above) or merging duplicates
   (`dedupe_existing.py`) gives the same event a new id. Everything that
   stores an id breaks:
   - a collection item (`ev_<id>` in DynamoDB): its copy can never be
     matched to the event again, re-adding the show makes a duplicate, and
     collecting mode doesn't mark it as added;
   - calendar entries (the `.ics` UID is the id), so re-adding duplicates
     the calendar event;
   - `descriptions.json` keys (rebuilt every export, so harmless).
4. **Collections keep a frozen copy.** `canvas/api/snapshot.py` copies the
   event when it's added and never looks again, so a collection never shows
   a cancellation, new time or new venue.

## Goals

- An event keeps the **same id** across scrapes, database wipes and
  re-scrapes, as long as its source still lists it the same way.
- When an id does change (duplicates merged, rescheduled to a new time, the
  one-off re-key below), the old id **resolves** to the new one.
- The pipeline **updates** an event's details when its source changes them,
  and records what changed.
- The pipeline **notices** cancellations (explicit or by disappearance), and
  postponements and reschedules where the source makes them clear, without
  false alarms from flaky or partial scrapes.
- The **main site** shows cancelled, postponed and rescheduled events
  honestly, and stops showing events that are gone.
- **Collections** show the current state of every Agora event in them
  ("Cancelled", "Moved to Sat 8 PM", "Venue changed", "No longer listed")
  instead of a frozen copy.

**Not in scope:** detecting changes to events whose source failed this run
(we learn nothing); undoing an automatic decision by hand (beyond the
existing Neon SQL); notifying people (email, push).

## Design

### 1. Stable ids

**Rule.** A new row's id is `uuid5(AGORA_NS, key)` (still a UUID, so the
column and every consumer stay as they are), where `key` is built from the
**creating source** (the row's first source) and the event's identity in
that source:

```
url present:  "<source>|url|<url>|<start_time as UTC ISO, seconds>"
no url:       "<source>|title|<normalized title>|<start_time as UTC ISO>"
```

`AGORA_NS` is a fixed UUID constant in code. Normalized title: casefold,
collapse whitespace, strip punctuation at the ends (reuse `dedup`'s
normalizer if one fits). `(url, start_time)` is already unique for rows with
a URL, so ids can't collide for them; two URL-less rows from one source with
the same title and time are the same event.

Consequences:
- A wipe and re-scrape gives the same ids, because sources are saved in
  `sources.txt` order and the same source creates the row again.
- It doesn't hold if, after a wipe, the creating source no longer lists the
  event but a later source does: that row gets a new id. Rare; accepted.
- A **new start time is a new id** (key includes the time). That is right:
  a rescheduled performance is a different instance. §3 links the two.

**Aliases.** New table `event_aliases(old_id PK, new_id, reason, at)`.
`reason` ∈ `merged` (duplicate merging), `rekeyed` (the one-off below),
`moved` (rescheduled, §3). Resolution follows chains (with a cycle guard)
to the current id. `dedupe_existing.py` writes `merged` aliases for every
row it deletes.

**One-off re-key** (`service/rekey_events.py`, modelled on
`dedupe_existing.py`: dry run by default, `--apply` to change): for every
row whose id isn't its computed id, change the id (inside one transaction)
and record a `rekeyed` alias. If the computed id already belongs to another
row, report it and leave both alone (that's a missed duplicate). The owner
runs it once against Neon after the code ships; the frontend and API keep
working before and after, because of aliases.

### 2. Updates and seen-tracking in the save path

New columns on `events` (all nullable / with defaults so old rows work):

| Column | Meaning |
|---|---|
| `status` | `scheduled` (default), `cancelled`, `postponed`, `moved`, `unlisted` |
| `status_at` | when `status` last changed |
| `changed_at` | when a listed field last changed |
| `changed` | JSON: `{field: previous value}` for the most recent change |
| `seen` | JSON: `{source: ISO time}`, last successful scrape that listed it |
| `misses` | JSON: `{source: n}`, consecutive successful scrapes of that source that *should* have listed it and didn't |

Schema changes must apply to the existing Neon table: `db.init_db()` only
creates missing tables and indexes, so add an idempotent column migration
there (`ALTER TABLE events ADD COLUMN IF NOT EXISTS …` on Postgres; the
SQLite used by tests gets the columns from `create_all`). Same for the new
`event_aliases` table (created by `create_all`).

`save_events(raw_events, source)` changes:
- **Same source, existing row** (today's "skipped"): if the event's
  **creating source** is this source, compare `title`, `location`, `url`,
  `description`, `image_url`; if any differ, update them, set
  `changed_at`, and store the previous values in `changed`. Other sources
  never overwrite the creating source's fields (they only mark it seen).
  Count these as `updated` in the run report.
- **Every match** (same source or merge) and every insert: set
  `seen[source] = now`, `misses[source] = 0`; if the row's status is
  `unlisted`, set it back to `scheduled`: it was a flaky scrape.
- **Explicit status from the scraper:** `RawEvent` gains an optional
  `status` (`cancelled` or `postponed`). Scrapers that currently drop
  cancelled events emit them with the status instead. A shared helper also
  treats titles like `CANCELLED: …`, `[Canceled] …`, `POSTPONED - …` as that
  status (and strips the prefix from the title, so the id and matching are
  unaffected). An explicit status is applied immediately; the creating
  source listing the event again *without* one sets it back to `scheduled`
  (un-cancelled).

### 3. Disappearances (in the CI merge, after all sources are saved)

For each source whose result this run is `ok`, with `R` = the events it
returned:
- **Window:** only rows with `now < start_time <= max(start_time in R)`
  are judged. A source that only shows the next 3 weeks says nothing about
  week 5.
- **Partial-scrape guard:** if more than 20% (and more than 5) of the
  judged rows that list this source are missing from `R`, treat the scrape
  as partial: don't count misses for this source this run, and flag it in
  the merge report (and the run summary) as "possibly partial".
- Otherwise, for each judged row listing this source and not matched this
  run: `misses[source] += 1`.

A row becomes `unlisted` when **every** source on it has `misses ≥ 2`
(each judged in its own run), i.e. two consecutive good scrapes of every
source that listed it, all without it. Sources that failed or weren't
judged this run block the decision (no information is not evidence).

**Reschedules.** In the same pass, if a row from source S becomes
`unlisted` and S produced, in this run, exactly **one** new row with the
same URL and a different start time, and no other row of S still uses that
URL in the window: mark the old row `moved`, record a `moved` alias
old → new, and keep `changed = {"start_time": old start}` on the new row.
Ambiguous cases (several new times on one URL, a URL shared by many
showings) stay plain `unlisted`.

Reapplied each run, so a row that comes back is restored (§2).

### 4. What the manifest says

`events.json`:
- `events[]` keeps every `scheduled` event as today, and also includes
  `cancelled` and `postponed` events (with `"status"`) until their date,
  so people who saw them learn they're off. `unlisted` and `moved` rows are
  **not** in `events[]`.
- Each event changed in the last 7 days carries
  `"changed": {"at": …, "was": {field: old value}}` (only `title`,
  `location`, `start_time` are shown to people; the rest are kept for
  diagnostics).

New small file, `frontend/event-index.json`, for the collections API (and
anyone else who needs current facts by id, without descriptions):

```json
{"generated_at": "…",
 "events":  {"<id>": {"title","start_time","location","url","image_url","sources","status?","changed?"}},
 "gone":    {"<id>": {"status": "unlisted|moved|cancelled|postponed", "at", "title", "start_time", "moved_to?"}},
 "aliases": {"<old id>": "<current id>"}}
```

`gone` and `aliases` keep entries until the event's original date has
passed. The ship-manifest action commits this file with the others.

The ship guard (`ci.check_manifest`) still compares `events[]` counts, so a
mass `unlisted` (which the partial-scrape guard should already prevent)
also trips the existing 70% guard.

### 5. The main site (`frontend/index.html`)

- `cancelled` / `postponed`: the row stays, dimmed, with a **Cancelled** /
  **Postponed** badge, no "Add to calendar", and a collecting-mode button
  that still works (people may want to record it, but see collections
  below). Search and filters treat them like any event.
- `changed.was.start_time` (a rescheduled event's new row): a
  **Rescheduled** badge ("was Fri 7 PM") for 7 days. `changed.was.location`:
  **Venue changed**. Title changes: nothing shown.
- Rows not in the manifest simply aren't shown (as today).
- Collecting mode marks an event as added when the collection holds it
  under its current id **or any alias** of it.

### 6. Collections (`canvas/`)

The API keeps the copy made when an event was added (the "as added"
record), and **overlays the current state on every full read**:
- `snapshot.py` reads `event-index.json` instead of `events.json` (smaller:
  no descriptions, taxonomy, venues), with the same caching. Adding events
  uses it too (fixes "adding an event depends on the whole manifest").
- In `_view`, for each event item: resolve its event id through `aliases`;
  look it up in `events` or `gone`. Return, next to the stored `event`,
  `now`: `{status, start_time?, location?, title?, url?, moved_to?}` with
  only what differs from the copy, and `now.current_id` when it differs
  from the item's event id. If the index can't be fetched, return no `now`
  (the page shows the copy, as today).
- Adding an event whose id is an alias resolves it first, so it can't be
  added twice under two ids (the item id stays `ev_<id as added>`; the
  duplicate check also tries the current id).
- Polling (`?if_version=`) only tracks writes, so overlays refresh on the
  next full read (page load, or after any write). Good enough.

The page (`canvas.html`) shows, on the item card and its plan step:
- **Cancelled** / **Postponed** (red badge; the plan step too; excluded
  from "Add the plan to your calendar").
- **Moved to Sat 8 PM** with a **Use the new time** button: adds the new
  event (`moved_to`) and, if the old one is in the plan, replaces it in the
  same position; then removes the old item.
- **Venue changed** / **Time changed** ("was …"): show the current value,
  the old one struck through.
- **No longer listed**: "<Source> no longer lists this. Check the event
  page." with the link.
- Times and places everywhere (cards, plan, calendar export) use the current
  values when known.

## Phases and acceptance

1. **Stable ids + aliases.** uuid5 for new rows; `event_aliases`;
   `dedupe_existing.py` writes aliases; `rekey_events.py` (dry run + apply).
   Tests: the key rule (URL and URL-less; normalization), wipe-and-rescrape
   gives the same ids (SQLite), re-key creates aliases and skips collisions,
   chains resolve.
2. **Updates, seen-tracking, explicit status.** Columns + migration;
   same-source updates; `RawEvent.status`; title prefixes; the scrapers that
   drop cancelled events emit them instead (with their fixture tests).
   Tests for each rule, including "other sources don't overwrite".
3. **Disappearances + reschedules** in `ci.merge_results`; report fields
   (`updated`, `unlisted`, `moved`, `possibly_partial`) in the merge report
   and the data PR body. Tests: the window rule, two misses, the partial
   guard, failed sources block, comeback restores, reschedule pairing and its
   ambiguous cases.
4. **Manifest + index.** `status`, `changed`, `event-index.json` (events,
   gone, aliases) and its retention; ship-manifest commits it. Tests on the
   exporter.
5. **Main site.** Badges and dimming; collecting mode resolves aliases.
   Tests in `test_frontend.py`.
6. **Collections.** `snapshot.py` on the index; the `now` overlay; alias-aware
   adds; the page's badges, "Use the new time", plan and calendar behaviour.
   API tests (moto) + `canvas/scripts/e2e_frontend.py` cases.
7. **Docs.** CLAUDE.md ("saves never update rows" and the Neon delete
   workaround change), README → "Scheduled scraping" → Operations (what
   `unlisted` means, how to run the re-key, how to force a status by SQL),
   `canvas/HOW-IT-WORKS.md`, this spec's status, `future-features.md`.

**Rollout:** one branch and PR (it touches the jobs and the site). Test the
pipeline from the branch with `scrape.yml` (non-`main` runs use the
`ci-test` database and never deploy). After merging: let one daily run go
by, check the report's `updated` / `unlisted` / `possibly partial` numbers,
then run `rekey_events.py` (dry run, then `--apply`) against Neon.

## Decisions (settled with the owner, 2026-10-05)

- `cancelled` and `postponed` events **count** in the "N upcoming events"
  line and in filter counts: they're listed, so the numbers match the page.
- "Rescheduled" / "Venue changed" show on the main site for **7 days**
  after the change (and `changed` stays in the manifest for those 7 days).
- An `unlisted` event that its source lists again **just reappears**, with
  no badge.
