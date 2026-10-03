# Frontend payload: lean manifest, descriptions on demand

**Status: built (PR pending).**

## The problem

The page downloads all of `events.json` and indexes every field for search
before it is usable. At 5,060 events (2026-10-03) that is 7.3 MB on disk,
1.9 MB gzipped over the wire, and on a phone most of the cost is CPU.

Measured with headless Chromium, gzip-served like Pages, 390×844 mobile
viewport (Lighthouse's mobile profile: Slow 4G = 1.6 Mbps / 150 ms RTT, and
Fast 4G = 9 Mbps / 60 ms, both with 4× CPU slowdown):

| | download done | first row | search ready | blocked main thread* |
|---|---:|---:|---:|---:|
| Slow 4G phone | 11.2 s | **16.1 s** | 20.5 s | 6.1 s |
| Fast 4G phone | 2.2 s | **6.6 s** | 10.7 s | 5.7 s |
| Desktop | 0.9 s | 1.9 s | 2.5 s | 0.1 s |

\* total blocking time: the part of each long task over 50 ms.

**Descriptions are 64% of the bytes** and nearly all of the indexing work.
Rows only ever show a description's first sentence until "more" is tapped.
Shipping just that first sentence (same page code otherwise) measured:

| | first row | search ready | blocked |
|---|---:|---:|---:|
| Slow 4G phone | 16.1 → **5.5 s** | 20.5 → 7.1 s | 6.1 → 0.9 s |
| Fast 4G phone | 6.6 → **1.9 s** | 10.7 → 3.3 s | 5.7 → 0.7 s |

## Design

Two files, both written by the exporter, both shipped in the data PR:

- **`events.json`** — every upcoming event, as today, but without
  `description`. Instead: `summary` (the first sentence, ≤180 characters,
  cut at a word — exactly what a row shows collapsed) and `more: true` when
  the full text is longer. ~0.74 MB gzipped today. Everything else that reads
  it is unaffected: the canvas API copies id/title/time/location/url/image/
  sources, and the ship guard counts events.
- **`descriptions.json`** — `{event id: full description}` for events whose
  description is longer than its summary. ~1.35 MB gzipped. Fetched only
  when needed:
  - the first tap on "more" (the row shows "…" until it arrives), and
  - the first focus on search (or a shared `?q=` link).

**Search.** The first index covers title, summary, location, venue name,
sources and tag labels — searchable as soon as the page is. When
`descriptions.json` arrives, a complete index (adding full descriptions) is
built in the background in small chunks and swapped in; the active query
re-runs. Search keeps one index and AND semantics across all fields, so a
query matching words in the title and the description still matches.

**Indexing in small chunks.** `addAllAsync` chunks of 25 (was 250) keep each
task near or under 50 ms on a slowed phone, so typing stays responsive while
the full index builds (chunks of 50 measured 0.9 s of blocking during the
full-index build on a Fast 4G phone; 25 measured 0.2 s, for ~0.5 s longer).

**Old cached page + new manifest.** Pages caches for ~10 minutes; a page
loaded just before a deploy reads the new `events.json` and finds no
`description`. It then shows rows without descriptions until reload —
harmless, and gone within minutes.

## Result

Same harness, same 5,060 events, the exporter's actual split
(`events.json` 0.79 MB gzipped, `descriptions.json` 1.31 MB, fetched only
on demand):

| | first row | search ready | blocked |
|---|---:|---:|---:|
| Slow 4G phone | 16.1 → **6.3 s** | 20.5 → 8.2 s | 6.1 → **0.3 s** |
| Fast 4G phone | 6.6 → **2.1 s** | 10.7 → 4.5 s | 5.7 → **0.2 s** |
| Desktop | 1.9 → **0.6 s** | 2.5 → 2.0 s | 0.1 → 0.0 s |

After typing a search on the Fast 4G phone, the full-text index is swapped
in ~7.7 s later (download + background indexing) with 0.2 s of blocking;
until then results come from titles, summaries, venues and tags.

## Measuring

`scripts/measure_load.py` is the harness, committed with venues phase 4. It
serves a site directory gzipped and drives Playwright Chromium over CDP on
the profiles above, then reports medians for manifest download, first
row, search ready and blocking time. With `--map` it also times tapping
Map to pins on screen. Build a real-data site with
`scripts/preview_site.py` (`--keep-venues --page <old index.html>` gives
the "before" side). Its absolute numbers depend on the machine, so compare
before and after on the same one. On an M3 Pro (2026-10-03, 5,060 events,
median of 5):

| | first row | search ready | blocked |
|---|---:|---:|---:|
| Slow 4G phone | 4.9 s | 6.4 s | 0.2 s |
| Fast 4G phone | 1.2 s | 2.7 s | 0.2 s |
| Desktop | 0.1 s | 1.5 s | 0.0 s |

## Not now (if numbers grow)

- **Time window**: a first file with only the next 14 days (~0.25 MB
  gzipped) would get the first row on slow 4G to ~2.5 s, at the cost of
  filters/counts/day strip working on a partial set until the rest loads.
- **Prebuilt search index** shipped from CI (skips client indexing; a larger
  download than the data it indexes).
- **Service worker** to keep the last manifest offline.

## Testing

- Exporter: `summary`/`more` match the page's first-sentence rule
  (`firstSentence` in `index.html`); `descriptions.json` holds exactly the
  events with `more`; no `description` in `events.json`.
- Headless Chromium: rows render from the lean file; "more" fetches and
  expands the full text; search finds a word only in a full description after
  the full index swaps in; a `?q=` link works; no page errors.
- Load-time measurement before/after with the same harness (numbers in the
  PR).
