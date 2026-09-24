# Feature Spec: Recurring Events (bar trivia & weekly bar nights)

Status: Draft · Owner: Agora

## Goal

Cover the large class of events Agora currently misses: **recurring weekly bar
nights** — bar trivia first, then karaoke, bingo, drag, comedy, live music. These
aren't published as dated one-offs; they're "Trivia every Thursday 7:30pm at Bar
X." We need to (a) scrape directories that list these, (b) **expand each
recurrence into concrete upcoming dated occurrences** so they appear on the
day-grouped calendar, and (c) make them filterable (a `trivia` topic).

Motivating query: "all bar trivia in SF" → `?topic=trivia`, or "trivia tonight"
→ `?topic=trivia&dates=today`.

**Non-goals (this phase):**
- Per-occurrence detail (host, theme, prize) — the listing's static blurb is enough.
- Recurrences other than **weekly** (biweekly / "4th Thursday" / "Time TBA" are
  skipped in v1 — see Design decisions; revisit later).
- Ticketing / RSVP — these are drop-in.
- A general RRULE engine — we implement only what the sources actually use.

## Sources (spiked, both feasible)

- **SF Bar Guide** (`sfbarguide.com`) — *primary.* Static server-rendered HTML;
  "87 bars · 304 recurring events" across trivia/karaoke/comedy/drag/bingo/live
  music. Ships a JSON-LD `ItemList` of 87 `BarOrPub` entries (name, address,
  `/bar/<slug>` URL); per-event day/time/type live in the page markup and the
  per-bar pages. Broader than trivia — effectively *the* recurring-bar-nights
  dataset, of which trivia is a filter.
- **Sunset Trivia** (`sunsettrivia.com`) — *secondary.* A single trivia operator,
  ~155 venues with day/time. Next.js App-Router streaming app — data lives in
  `self.__next_f` RSC chunks (parseable but fiddlier than SF Bar Guide's HTML).
  Use it to fill venues SF Bar Guide misses.

## Design decisions

**A recurrence-expansion helper (`scrapers/recurrence.py`), not a new event
model.** A scraper parses a recurrence (weekday + time + cadence) and calls
`expand_weekly(weekday, time, venue, ...) -> list[RawEvent]` to get concrete
dated occurrences. Each occurrence is an ordinary `RawEvent` with a real
`start_time`; nothing downstream (save, classify, export, frontend) changes.
Pure and unit-testable (pass a fixed "today").

**Materialize a short rolling window, not the full look-ahead.** Weekly ×
365-day horizon = ~52 rows per event × hundreds of events = an explosion nobody
wants (no one plans trivia 6 months out). `RECURRENCE_HORIZON_DAYS = 28` (4
weeks). ~304 events × ~4 = ~1,200 occurrences — fine. Every scrape re-expands
`today → today+28d`, so the window rolls forward on each run; past occurrences
age out of the manifest via the exporter's pruning. This keeps the feature
incremental like the rest of the pipeline. (If no run happens for >28 days the
window empties — acceptable; we run regularly.)

**Occurrence identity must include the venue.** Dedup keys on `(title,
start_time)`. Two bars both running "Trivia Night" Tuesday 7pm would collide and
merge into one row. So the **title embeds the venue** — `"Trivia Night at The
Orbit Room"` (or `"The Orbit Room — Trivia Night"`) — making each occurrence
unique. `location` also carries the venue+address; `url` links to the bar's
`/bar/<slug>` page (or its own site when available).

**Weekly only in v1; skip what we can't place confidently.** Per Agora's
"never fabricate dates" rule: a plain weekly cadence is unambiguous (expand it).
But `biweekly` (which weeks? no anchor), `4th-of-month` (computable but deferred),
and `Time TBA` (no start time) are skipped with a logged reason rather than
guessed. Revisit monthly/biweekly once we see how much we're dropping.

**Stale-night limitation (accepted for v1).** If a venue moves trivia Thu→Wed,
already-saved future Thursday occurrences linger until they pass (dedup skips,
doesn't update). Rare; the wrong rows self-expire within 28 days. A future
improvement: a scraper that deletes its own future occurrences before re-adding.

**Source labeling.** Scraper `NAME` = `"SF Bar Guide"` / `"Sunset Trivia"` (the
aggregator), with the venue in the title + location. SF Bar Guide is a curated
directory ("verified weekly"); link back to it. (Alternative — per-venue NAME —
rejected for v1: it fragments the Sources filter into ~90 one-off entries.)

**Tagging.** These flow through the normal classifier (their blurb — event name
+ venue + day/time — is enough signal), so no bespoke tagging path. But the
taxonomy needs new **topics**: `trivia`, `karaoke`, `bingo`, `drag`. Type is
mostly `social` (`games-hobby` for trivia/bingo, `party-club` for
karaoke/drag). Add each source's one-line `source_profiles.json` prior. Taxonomy
edit is additive → follow the runbook in `service/taxonomy.py` (edit
`taxonomy.v1.json` in place; reclassify to apply).

## Data model

No schema change. Each occurrence is a `RawEvent`:
- `title`: `"<Event Name> at <Venue>"` (venue-embedded for unique dedup).
- `start_time`: concrete UTC datetime of the occurrence (source-local → UTC).
- `location`: `"<Venue>, <address>"`.
- `url`: the bar's directory/own page.
- `description`: the listing blurb (event type, day/time, any note).
- `image_url`: usually none.

## How it fits the pipeline

Unchanged: `scrape → save_events → classify_new_shows → export`. The recurrence
expansion happens *inside* the scraper's `scrape()`, before returning
`RawEvent`s. Classification keys per `(source, title)` — since title embeds the
venue+event, each distinct trivia night is one classified "show" whose weekly
occurrences all share the tag (exporter joins per occurrence). So a bar's
trivia is classified **once**, not once per week — the cache grain still holds.

## Phasing

- **A — recurrence helper + SF Bar Guide.** `scrapers/recurrence.py`
  (`expand_weekly`, weekday/time parsing, cadence gating) + `scrapers/sfbarguide.py`
  (JSON-LD bar list → per-bar day/time/type → expand). Add `trivia`/`karaoke`/
  `bingo`/`drag` topics + the source profile. Register + `sources.txt`. Ship
  trivia into the manifest, verify `?topic=trivia` works.
- **B — Sunset Trivia.** Parse the RSC/`__next_f` payload for its ~155 venues;
  reuse the recurrence helper. Second source; dedups against SF Bar Guide on
  `(title, start_time)` where they overlap.

Do A first and confirm occurrences land correctly (right dates, unique per
venue, tagged) before B.

## Testing

- `recurrence.expand_weekly`: given a frozen "today", a weekday+time expands to
  the correct set of dates within the horizon (count, weekday, tz→UTC); a
  cadence it can't handle returns `[]` with no crash; DST boundary correctness.
- `sfbarguide.parse`: from a trimmed real fixture — bar name/address/url from
  JSON-LD, day+time+event-type from the markup; title embeds the venue; unknown
  cadence rows skipped.
- Dedup: two venues' "Trivia Night" at the same time stay distinct rows.
- Classifier: a trivia occurrence tags `social`/`games-hobby` + topic `trivia`.
- No live network in tests; verify `scrape()` live once.

## Resolved decisions

- **Horizon: 28 days** (~1,200 occurrences; re-run at least monthly).
- **Scope: all of SF Bar Guide** — trivia, karaoke, bingo, drag, comedy, live
  music (the full ~304-event directory), not trivia-only. New topics to add:
  `trivia`, `karaoke`, `bingo`, `drag`. Comedy maps to the existing `comedy`
  topic; live-music nights use existing music topics (or none when no genre is
  given — the classifier decides).
- **Fetch cost: whatever the data requires** — if the homepage carries day/time
  for all events, one fetch; if not, fetch each `/bar/<slug>` concurrently (a
  ThreadPoolExecutor, like SFPL). The build's first step confirms which.

## Still to verify during build

- **Sunset Trivia RSC parsing** stability — the `__next_f` payload shape can
  change; may need a headless render fallback (Phase B).
- **Attribution** expectations for SF Bar Guide (a curated third-party directory).
