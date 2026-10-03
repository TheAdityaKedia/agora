# Venues and the Area filter

**Status: phases 1–3 shipped (#54: resolver + owner-reviewed venue list;
#56: pipeline, review issue, Area filter; #60: AI-assisted resolution,
venue-aware dedup, venue names and links — see "Phase 3 design"). Phase 4
(maps) is next.** Decisions settled with the owner: OpenStreetMap
(Nominatim) for geocoding; build the venue list now rather than a
location→area lookup only; nobody reviews the auto-merged data PRs, so
the pipeline must verify new places itself and route only real doubts to a
person.

## The problem

Events carry `location` as free text written by ~60 scrapers, email submissions
and aggregators. An Area filter (SF / East Bay / …) needs to know *where* each
event is, and needs to be right: a filter that silently drops or misfiles
events is worse than none.

Measured on the 2026-10-02 manifest (5,011 events):

- **430 distinct location strings.** Small enough to resolve each once, cache
  it, and have a person review the initial set.
- **Matching city names in the text covers 79%.** The rest are venue names
  with no city — `SFPL — Main` (198 events), `SFJAZZ Center — Miner
  Auditorium` (134), `Z Below`, `Sydney Goldstein Theater`, `Orpheum
  Theatre`.
- **Sources don't pin the place.** 23 of 62 sources list events at several
  locations: SF Bar Guide (88 distinct), Partiful (64), Litquake (58), SFPL
  (31 branches). Single-venue sources hold off-site events too (Commonwealth
  Club at Dominican University, San Rafael; Black Bird Bookstore at the Sydney
  Goldstein Theater).
- **Nominatim resolves venue names well** (Orpheum → 1192 Market St, Z Space
  → 450 Florida St, SFJAZZ → 201 Franklin St, "San Francisco Public Library
  Main" → 100 Larkin St) but misses renamed places (Sydney Goldstein Theater,
  formerly the Nourse, has no result). Its results omit `county` for San
  Francisco (a consolidated city-county), so regions come from coordinates,
  not address fields.

## Goal & non-goals

**Goal:** a venue list as the single source of truth for *where* events happen,
an Area filter built on it, and a resolution pipeline whose failure mode is
"area unknown" (visible, harmless), never "wrong area" (silent).

**In scope (phases 1–2):** venue registry, location→venue mapping, automated
resolution with evidence rules, review queue, coverage alerts, region-level
Area filter.

**Later phases (designed for, not built first):** AI-assisted resolution of
hard strings, venue-based dedup, canonical venue names in the UI, map view,
"near me", SF neighbourhoods, venue pages.

**Non-goals:** geocoding user locations server-side; routing/transit; changing
how scrapers report locations (they keep emitting free text).

## Design decisions

1. **The venue list is a committed file, not a Neon table.** Same reasoning as
   `classifications.json`: local, `ci-test` and production runs all see the same
   venues; fixes are made in GitHub's web editor (works from a phone) with
   history and revert; it honours "the DB is working state, durable data is
   committed". *Alternative considered:* a Neon `venues` table — queryable and
   conflict-free, but ephemeral locally, divergent on `ci-test`, edited by SQL
   with no history. If DB-side joins are needed later, the merge job loads the
   file into a table each run (file stays the source of truth).
2. **Resolution is joined at export time, never written into event rows.**
   Saves never update rows, so storing a venue on the event would freeze
   mistakes. Mapping location string → venue in a committed file means a
   correction applies to every event at the next export.
3. **Unknown beats wrong.** A string is assigned only when independent signals
   agree; otherwise it waits in the review queue and its events have no area.
4. **Regions come from coordinates and county boundaries,** not from address
   fields or text alone — deterministic point-in-polygon against Census
   county shapes.
5. **Splitting is cheap, merging is expensive.** A new string joins an existing
   venue only on strong evidence; otherwise it becomes a new venue flagged as a
   possible duplicate. A wrong merge spreads (dedup, venue pages); a split is
   fixed by adding an alias.
6. **The AI model proposes, OpenStreetMap disposes.** Haiku may turn a venue
   name into an address, but coordinates always come from Nominatim, and the
   result must still pass the evidence rules.
7. **Human edits are validated.** A schema/consistency test runs on the
   committed files, and the merge job refuses a malformed file (keeps the last
   good one and alerts) rather than shipping broken areas.

## Regions

| id | Label | Counties |
|---|---|---|
| `sf` | San Francisco | San Francisco |
| `eastbay` | East Bay | Alameda, Contra Costa |
| `peninsula` | Peninsula | San Mateo |
| `southbay` | South Bay | Santa Clara |
| `northbay` | North Bay | Marin, Sonoma, Napa, Solano |
| `online` | Online | — (no physical place) |

Coordinates outside these nine counties are rejected as "not in the Bay Area"
(the email-ingest validator already refuses such events). Shapes:
`service/data/geo/bay_area_counties.geojson`, simplified from the US Census
cartographic boundary files (public domain), point-in-polygon in pure Python
(no new dependency).

## Data model

Two committed files in `service/data/`, pretty-printed and key-sorted so
re-saves are byte-identical (like `classifications.json`).

### `venues.json` — the registry (mostly human-curated)

```json
{
  "version": 1,
  "venues": {
    "sfjazz-center": {
      "name": "SFJAZZ Center",
      "address": "201 Franklin St, San Francisco, CA 94102",
      "lat": 37.77634, "lng": -122.42171,
      "precision": "building",
      "region": "sf",
      "osm": "way/123456789",
      "status": "verified",
      "evidence": ["geocode:poi name=SFJAZZ Center county=San Francisco", "text:none", "source:SFJAZZ Center home=sf"],
      "added": "2026-10-03"
    }
  }
}
```

- `id`: stable slug, never reused.
- `precision`: `building` (POI or house-number match) · `street` · `city`
  (centroid only). Only `building` gets a map pin and, later, a neighbourhood;
  every precision gives a region.
- `status`: `verified` (a person checked it) · `auto` (passed the evidence
  rules unattended). Both are used; the distinction drives spot-checks and
  lets a person see what has never been looked at.

### `venue_locations.json` — string → place (mostly machine-maintained)

```json
{
  "version": 1,
  "locations": {
    "sfjazz center — miner auditorium": {"venue": "sfjazz-center", "room": "Miner Auditorium"},
    "online via zoom":                  {"place": "online"},
    "san francisco":                    {"place": "region", "region": "sf"},
    "various locations":                {"place": "none"},
    "sydney goldstein theater":         {"pending": {"reason": "no geocode result", "first_seen": "2026-10-03", "last_tried": "2026-10-03", "events": 14, "sources": ["Black Bird Bookstore", "City Arts & Lectures"]}}
  }
}
```

Keys are **normalised** location strings: Unicode NFKC, lowercase, curly →
straight quotes, collapsed whitespace, trailing `, USA` and ZIP stripped. One
entry kind per key: `venue` (+ optional `room`), `place: online`,
`place: region` (city-level only, no venue), `place: none` (TBA / various /
hybrid), or `pending`.

### Manifest additions

`events.json` gains a top-level `venues` map (id → name, region, and later
address/lat/lng) and, per event, `venue` (id or null) and `region` (id or
null). Per-event cost is ~30 bytes; venue details are stored once.

## Resolving a new location string

Runs in the merge job (the single writer), after saving events and before
export, only for strings with no entry. Steps in order; the first conclusive
one wins.

1. **Known string** — normalised key already in `venue_locations.json`.
2. **Not a place** — online/virtual/Zoom/livestream → `online`;
   TBA/various/"see event page" → `none`; online *and* a place → `none`
   (hybrid).
3. **Room of a known venue** — `<venue alias> — <room>` / `<room> at <venue>`
   patterns against existing venue names and aliases (`SFPL — Main`,
   `Z Below` once added as an alias).
4. **Geocode the string** with Nominatim (Bay Area `viewbox`, `bounded=1`,
   `addressdetails=1`), then apply the evidence rules below.
5. **AI-assisted (phase 3)** — Haiku, given the string, source and source
   profile, returns `{kind, venue_name, street_address, city}`; the address is
   geocoded and must pass the same evidence rules. Never supplies coordinates.
6. **Otherwise** → `pending`.

### Evidence rules

Independent signals:

| Signal | Meaning |
|---|---|
| **T** text city | a Bay Area municipality named in the string → its county (committed city→county table, reusing `scrapers/bay_area.py`'s list) |
| **G** geocode | Nominatim point → county by polygon; precision from result class (POI / house → building, road → street, city/suburb → city) |
| **N** name match | normalised name tokens of the string overlap the OSM result's name (needed when the result is a POI) |
| **S** source home | per-source home region, only for sources whose events are almost all at one venue (weak; a tiebreaker, never sufficient) |
| **V** near a venue | an existing venue within 100 m with a similar name |

Accept automatically (`status: auto`) when **all** hold:

- **G** is inside the nine counties;
- **G** agrees with **T** when the text names a city;
- the geocode is believable on its own terms: a POI result needs **N**; a
  house-number match needs the number to appear in the string; a city-level
  result is accepted only as `place: region` (no venue);
- at least two signals agree on the county (G plus T, N-matched POI, or S).

Then: **G**+**V** with **N** → add the string as an alias of that venue;
otherwise create a new venue (and list it as a possible duplicate if any venue
is within 250 m). Everything else → `pending`, with the disagreement as the
reason ("text says Oakland, geocode in San Leandro").

### Politeness and cost

Nominatim's usage policy: ≤1 request/second, an identifying User-Agent,
cache results, no bulk jobs. The merge job caps lookups per run (50; the rest
roll to the next run), sleeps 1.1 s between calls, and retries `pending`
strings at most weekly. Steady state is a few new strings a day. OpenStreetMap
data is ODbL: the site footer gains "Venue data © OpenStreetMap contributors"
once addresses or maps are shown.

## Review queue and alerts

- **"Places to review" issue** (GitHub, like "Scrape failures"): opened or
  updated by the merge job, @mentions the owner, closed when nothing is
  pending. Never blocks shipping. Each entry: the string; sources and event
  count; the proposed venue, address and region if any; an OpenStreetMap link;
  the reason it's waiting. A fix is an edit to `venue_locations.json` (map the
  string to a venue, or to `none`) or a new entry in `venues.json`; it takes
  effect at the next run.
- **Coverage alert:** the run goes red (after data ships) when more than 3% of
  upcoming events have no region, or more than 20 strings go pending in one run
  (usually a source changed its location format).
- **Spot-checks:** `python -m places report --status auto --since 7d` lists
  venues added unattended, with map links, for an occasional look.

## How it fits the pipeline

- `service/places/` package: `normalize.py`, `regions.py` (counties,
  point-in-polygon, city→county), `geocode.py` (Nominatim client: rate limit,
  User-Agent, viewbox), `resolve.py` (steps + evidence), `store.py`
  (load/validate/save both files), `review.py` (issue body), `__main__.py`
  (CLI: `resolve`, `report`, `validate`).
- `ci.merge_results`: save → classify → **resolve places** → export. Failures
  in resolution never stop the export (same guard as classify). The data PR
  adds `service/data/venues.json` and `venue_locations.json` to its commit.
- `main.run()` (local): same step, `--no-places` to skip; with no network the
  committed files are still used for the export.
- `scrape.yml`: the alert step gains the "Places to review" issue and the
  coverage check; `ci.py guard` is unchanged.
- Email ingest: submissions resolve through the same step on their next
  export (the ingest job exports too).

## Frontend

- **Area filter** in the filter sheet / sidebar: chips for the six regions
  (OR within the facet), shown with event counts; URL `?area=sf,eastbay`.
- When the filter is active, events with no region are hidden and a line under
  the count says how many ("12 events have no area yet").
- Location text on rows is unchanged in phase 2 (canonical names are phase 3).

## Bootstrapping — the one full human review

A one-off `python -m places resolve --all --report bootstrap.md` resolves the
current 430 strings with the same rules (plus a hand-written alias list for the
known venue-name cases: SFPL branches, SFJAZZ rooms, Z Space stages, ATG
theatres, War Memorial venues). It ships as its own PR containing both files
and a review table grouped by source — map link per venue, uncertain ones
first. That PR is the only time every place is checked by a person; the
`status` of everything reviewed becomes `verified`.

## Phasing

1. **Venue registry + bootstrap** — `service/places/` (normalise, regions,
   geocode, resolve steps 1–4, store/validate), county shapes, the bootstrap
   PR with all current strings reviewed. No pipeline or UI change.
2. **Pipeline + Area filter** — resolution in the merge job, manifest
   `venues`/`venue`/`region`, review issue, coverage alert, frontend filter.
3. **Hard strings + venue identity** — AI-assisted step; dedup compares venue
   ids when both sides are resolved (`dedup.locations_agree`); canonical venue
   names on rows; venue links ("everything at this venue").
4. **Maps** — coordinates in the manifest, map view, "near me" (browser
   geolocation, client-side only), SF neighbourhoods (DataSF Analysis
   Neighborhoods).

## Phase 3 design

Four parts, each usable on its own.

### AI-assisted resolution (step 5)

- **When:** only for a string the rule-based steps would leave `pending`
  (not for `outside`), and at most **20 model calls per run** (the rest wait
  for the next run, like the lookup cap). Skipped without Bedrock credentials.
  Pending strings are retried weekly, so a string costs at most one call a week.
- **Ask:** Haiku (the classifier's model and client), given the string, its
  sources and their `source_profiles.json` lines, returns JSON
  `{"kind": "venue" | "not_a_place" | "unknown", "name", "street_address", "city"}`.
- **Check:** the proposal only adds map queries (`name, city`;
  `street_address, city`). A result is accepted only by the *existing* evidence
  rules, judged against the **source's own text**, not the model's: a POI must
  match the name in the string (N), a house number must appear in the string,
  a city in the string must agree, the source home must agree. So the model can
  find "Sydney Goldstein Theater" at 275 Hayes St, but can't by itself make a
  venue of it: a model-only address never counts as a signal.
- **Otherwise** the string stays `pending`, now with the model's proposal
  (`pending.suggestion`: name, address, city, and the map result it led to, if
  any) shown in the "Places to review" issue, so a review becomes "confirm or
  correct". `not_a_place` is a suggestion too, never applied automatically.
- Evidence on venues it helped find records `ai: proposed <name>, <address>`.

### Venue-aware dedup

Save-time dedup (`dedup.locations_agree`) first asks the committed files: when
**both** strings map to venues, they agree iff the venue ids match (and, when
both name a room, the rooms match). Otherwise the text rules apply as today.
So "Bottom's Up, 4704 Mission St." and "Bottom's Up Bar" agree, and two SFPL
branches never do. Known entries only — dedup never triggers a lookup. A
string first seen in this run is unresolved at save time (resolution runs in
the merge job, after saving) and falls back to text.

### Canonical venue names on rows

- Export adds `room` per event (when the string names one) and `address` to the
  manifest's `venues` map.
- A row with a venue shows **`<venue name> · <room>`** instead of the source's
  text; the source's text and the address are the button's tooltip. Rows
  without a venue (region-only, unknown) show the text as today.
- Search indexes the venue name as well as the source's text.
- Add to calendar / Google Calendar use `<name>, <address>` when known.
- The footer gains "Venue data © OpenStreetMap contributors" (ODbL), now that
  addresses are shown.

### Venue links ("everything at this venue")

Clicking a venue name filters to that **venue** — every spelling and room —
not just that exact string. URL `?venue=<id>` (bookmarkable; an unknown id is
ignored); the pill reads the venue's name. Rows without a venue keep today's
exact-text filter (`?loc=`). Dedicated venue pages wait for phase 4 (maps).

## Testing plan

- **No network in tests:** Nominatim responses are recorded JSON fixtures
  (SFJAZZ, Orpheum SF, an Orpheum-in-LA response, Mighty Mighty Studio,
  a no-result case, a city-level result).
- Normalisation cases (curly quotes, ZIPs, em-dash rooms).
- Point-in-polygon: known points per county; points just outside (Santa Cruz,
  Sacramento); the Golden Gate Bridge (on the SF/Marin line).
- Evidence rules: every acceptance path and every pending reason, including
  Orpheum-in-LA rejected and text/geocode disagreement.
- Alias vs new venue: same name within 100 m → alias; similar name at 300 m →
  new venue + possible-duplicate.
- Data-file validation: unknown venue ids, invalid regions, coordinates outside
  the counties, duplicate ids, malformed pending entries.
- Export join: manifest `venues`/`venue`/`region`; a correction in
  `venue_locations.json` changes the next export without touching the DB.
- Frontend (headless Chromium): area chips, URL state, unknown-area note.

## Open questions

- A weekly digest of `auto` venues in the review issue, or only the on-demand
  report?
- Is 3% the right unknown-area threshold once phase 1 shows real coverage?
- Show event counts per region chip, or keep chips plain like the presets?
