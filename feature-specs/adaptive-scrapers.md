# Adaptive / self-healing scrapers

**Status: early design — brainstorm captured, key decisions deliberately
deferred.** This spec records the problem, the shape we agreed on, and the forks
we chose *not* to settle yet. It is **not** ready to turn into an implementation
plan. A future brainstorming session should resolve the "Open decisions" below
before any code is written.

## The problem

Every scraper today is a **fixed code path**: it hard-codes where a source's
events live (a URL) and how to parse them (selectors / JSON shape). That's the
right call for stable sources — but sources change over time, and when they do,
the scraper fails **silently**: `scrape()` returns `[]` (or garbage), the run
continues, the manifest is rebuilt from the surviving DB rows, and nobody
notices the source quietly went dark.

Concrete triggers we've already hit or foresee:

- **Config / URL drift.** The parser is fine but a pointer changed. Litquake is
  an annual festival whose schedule lives at `litquake2026.sched.com`; next year
  it's `litquake2027.sched.com`. The scraper carries a code comment reminding a
  human to bump the URL every year — a manual, forgettable chore.
- **Structural / markup drift.** A site redesign or JSON-shape change breaks the
  selectors/paths; the scraper yields 0 events or malformed ones.
- **Content / quality drift.** The parser still runs but output degrades — dates
  off by a year, new boilerplate polluting descriptions, off-target events
  creeping in.

The insight driving this feature: **some sources want a fixed code path; others
need something that can adapt as the source changes.** The goal is to keep the
cheap deterministic path for stable sources and add intelligence only where
sources actually drift.

## Goal & non-goals

**Goal:** make scraper breakage *loud* instead of silent, and make the fix a
fast, reviewed change instead of a manual re-investigation — using an AI agent
to do the investigation/repair, and (eventually) a runtime fallback to keep the
site populated while a durable fix lands.

**In scope (failure modes to handle):**
- Config / URL drift
- Structural / markup drift
- Content / quality drift

**Non-goals:**
- **Cold-start generation** (writing a brand-new scraper for a never-seen source
  from just a URL). We already do this ad hoc with dispatched subagents
  (El Rio, Kronos); it's a separate feature. This spec is about **maintaining
  existing scrapers as their sources change.**
- Replacing deterministic scrapers wholesale. Fixed code paths remain the
  default and the majority.

## Agreed direction

These are settled enough to build the spec around; the *sequencing* and the
runtime-fallback details are still open (see "Open decisions").

### 1. Tiered / hybrid intelligence (the north star)

Two tiers working together:

- **Maintenance-time repair (durable fix).** Runtime stays deterministic code.
  When a source drifts, an AI agent investigates the changed site and proposes a
  **code/config fix — a diff + updated fixture + passing test** — that a human
  reviews and merges. AI runs only in the repair loop, so steady-state cost is
  ~$0 and the committed scraper stays reviewable code.
- **Runtime adaptive fallback (keep-alive).** When deterministic parsing yields
  nothing/garbage, the scraper falls back to an LLM that interprets the fetched
  page into `RawEvent`s on the spot, keeping events flowing until the durable
  fix lands. Costs per-run, is non-deterministic, and is lower-trust — so it's
  the tier we're most cautious about and likely sequence last.

The same detected failure ideally does both: fallback keeps today's manifest
populated **and** files a repair task so the deterministic fix still lands.

### 2. Detection is source-dependent — a per-source *expectation model*

There is no single "this drifted" rule. The trigger depends on what the source
*is*. At least two archetypes:

- **Continuous venues** (theaters, music halls, bars, galleries): steady event
  volume is expected. A drop **below a per-source baseline** (0, or far under its
  norm) is a strong drift signal. This is the common "silent empty" break.
- **Episodic / seasonal sources** (Litquake and other annual festivals): zero
  events for most of the year is **normal**, so volume tells you nothing. These
  instead need a **periodic discovery check** — "is there a current edition yet,
  and what's its URL?" — and their real failure mode is the **pointer going
  stale** (this year's subdomain/path), not an empty parse.

So drift detection is driven by a **per-source expectation profile**, not a
global threshold. Candidate signals a profile can opt into:

- **Volume vs. baseline** — needs a stored per-source recent-count baseline.
- **Per-event validation** — fraction of events failing sanity checks
  (non-empty title, `start_time` in a plausible window, well-formed URL,
  non-boilerplate description). Catches quality drift when volume looks fine.
- **Fetch / exception signals** — non-200s, WAF blocks, scraper exceptions
  (a 404 on the calendar URL ⇒ config drift, distinct from an empty parse).
- **Scheduled re-verification** — even when healthy, periodically have an agent
  sanity-check a live sample to catch slow/subtle content drift; for episodic
  sources this doubles as the "is there a new edition / new URL?" discovery
  check.

We likely extend the existing **`source_profiles.json`** (today: one-line venue
priors for the tagger) — or a sibling registry — with an *expectation* block per
source: its archetype (continuous vs. episodic), which drift signals apply, and
any baseline/cadence metadata. Exact shape is an open decision.

### 3. Phased autonomy — local now, CI later

- **Phase (early): human-triggered, local.** Detection just *flags* the source
  (log / report / queue). A human runs a `heal <source>` command that dispatches
  a repair agent locally (the way El Rio/Kronos were built), which produces a
  diff + fixture + test for human review. Matches today's local-docker workflow;
  cheapest and safest.
- **Phase (later): autonomous in CI.** A scheduled CI job scrapes, detects
  drift, and dispatches the same repair agent unattended to open a PR
  automatically; humans only review PRs. Bigger lift: agent + AWS creds in CI,
  guardrails, a scheduled runner (Agora has **no scheduled scrape today** — the
  manifest is refreshed manually/locally).

## How it fits the pipeline

- Detection slots in right after each source's scrape completes in
  `main.run()` — we already have per-source counts and isolated per-source
  exception handling there; drift detection consumes those plus the stored
  baseline/profile.
- The runtime fallback (if/when built) slots **inside** a scraper's `scrape()`
  (or a shared wrapper) as the "deterministic parse returned nothing → try LLM"
  branch, analogous to how `classify.py` already calls Bedrock.
- The repair agent reuses the existing subagent-dispatch pattern and the
  `CONTRIBUTING.md` data-source ladder; its output is an ordinary scraper diff.
- Baselines/expectation data are **committed** (like `classifications.json` and
  `source_profiles.json`), since the DB is ephemeral.

## Open decisions (defer to a future brainstorming session)

1. **Sequencing of the tiers.** Three candidate paths discussed:
   - **A (leading recommendation):** detection + healing first; runtime fallback
     deferred. Rationale: Agora ships a *committed, manually-refreshed* manifest,
     not a live backend, so there's little user impact in the gap between a break
     and the merged fix — the last good manifest keeps serving. The high-value,
     low-risk 80% is "stop failing silently + make the fix a reviewed 2-minute
     diff." Runtime fallback (costly, non-deterministic, low-trust) comes last,
     only if proven needed.
   - **B:** full hybrid up front (runtime fallback + detection + repair
     together) — most resilient, but front-loads the costly/non-deterministic
     part and delays the reviewable-fix win.
   - **C:** detection-only (make breakage loud; keep repair fully manual) —
     cheapest, but leaves the "intelligent scraper" idea on the table.
2. **Expectation-model shape & home.** Extend `source_profiles.json` vs. a new
   registry; exact schema for archetype + signals + baseline/cadence.
3. **Baseline storage & maintenance.** Where the per-source volume baseline
   lives, how it's seeded, and how it updates without masking a real slow
   decline.
4. **Episodic-source discovery.** How the agent finds "the current edition's
   URL" from a stable landing page, and whether that becomes an automatic
   `sources.txt` update or a flagged suggestion.
5. **Runtime-fallback trust model.** If built: do LLM-parsed events get flagged
   as provisional in the manifest/UI? Per-run cost ceiling? Per-source opt-in?
6. **Which sources opt in.** Default all sources to detection; adaptive/fallback
   likely per-source opt-in (stable sources stay pure deterministic code).

## Testing plan (once decisions are made)

- Detection logic is pure and unit-testable: feed synthetic (baseline, count,
  events) tuples per archetype and assert the drift verdict.
- Per-event validation is pure: fixtures of good vs. drifted events.
- The `heal` command's agent output is verified the normal way — the generated
  scraper diff must come with a passing test against a fresh real fixture.
- Runtime fallback (if built) needs an injectable LLM client so tests never hit
  the network (mirror `classify.py`'s design).

## References

- `CONTRIBUTING.md` — the scraper contract + data-source priority ladder the
  repair agent follows.
- `service/data/source_profiles.json` — existing per-source metadata (tagging
  priors); candidate home for the expectation model.
- `service/classify.py` — existing pattern for an injectable, cached Bedrock
  call; the runtime fallback and any LLM step should mirror it.
- `service/main.py::run` — where per-source counts + isolated failures already
  exist; the natural hook for detection.
