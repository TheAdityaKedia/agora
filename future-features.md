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
- **Product analytics** — cookieless, first-party usage analytics: visits and
  geography, what people search, filter and click (events, tags, sources,
  venues, areas, normalized by supply), engagement funnel, and later CTR,
  real-visitor performance and errors; private weekly digest email.
  Spec: [`feature-specs/analytics.md`](feature-specs/analytics.md) (draft).
- **Adaptive / self-healing scrapers** — detect when a source's markup or
  endpoint changes and adapt instead of silently returning `[]`.
  Spec: [`feature-specs/adaptive-scrapers.md`](feature-specs/adaptive-scrapers.md)
  (early design; open decisions to settle first).
- **Collections (event canvases)** — save events for yourself, or share a
  collection so friends add events, vote 👍 by name, comment and pick a plan;
  duplicate / share a copy. Agora's first live backend (AWS Lambda +
  DynamoDB). Next: a one-tap ☆ Save on every event card.
  Spec: [`feature-specs/event-canvases.md`](feature-specs/event-canvases.md);
  how it works now: [`canvas/HOW-IT-WORKS.md`](canvas/HOW-IT-WORKS.md).
  **Known gaps** (found 2026-10-04; none has a spec yet), most important first:
  - Spec for the first two: [`feature-specs/event-lifecycle.md`](feature-specs/event-lifecycle.md)
    (stable ids, updates, cancellations and disappearances, shown on the
    main site and in collections).
  - *Event copies drift.* An added event is a copy made at that moment;
    a later cancellation, new time or new venue never reaches it. Worse,
    upstream: saves never update rows and nothing removes events that vanish
    from their source, so a cancelled show stays on the **main site** too.
    Fix both: the scrape marks vanished/changed events (e.g. `status`,
    `changed_at` in the manifest), and the API refreshes copies on read from
    its cached manifest, showing "Time changed" / "No longer listed".
  - *Event ids aren't stable.* Ids are random per database row
    (`uuid4`), and rows get deleted and re-scraped (the documented fix for
    stale fields) or merged by dedup. Then a collection's `ev_<id>` points at
    nothing, re-adding the same show makes a duplicate, and collecting mode
    doesn't mark it as added. Needs ids derived from the event (source + URL
    + start) or an old→new id map at export.
  - *Losing the link loses the collection.* Everything is per browser:
    clearing site data or a new phone loses "Your collections", the name and
    the default (the collections themselves survive). Options: email
    yourself your links, a recovery code, or accounts.
  - *No protection from a careless or hostile editor.* Anyone with the link
    can remove items, rename, rewrite the note or reorder the plan; names
    are self-declared; links get forwarded. Undo is per item only. Options:
    owner-only actions for name/note/delete, "restore everything removed
    since…", a report button.
  - *Nobody can delete a collection.* "Keep forever" plus no delete means
    names and comments persist; removal requests need `admin.py`. Add
    delete (owner devices) and say so in a privacy note.
  - *No alerts.* Only a $5 budget alarm. API errors, throttling and the
    AWS account's Lambda limit of 10 concurrent runs (a busy evening could
    hit it) go unnoticed. Add CloudWatch alarms; ask AWS to raise the limit
    before a public launch.
  - *Page and API deploy separately.* A page and the API can be a version
    apart for minutes (or longer in an open tab). Keep API changes additive
    and remove old fields only after a release that stopped using them
    (`winner_item_id` was removed in one step).
  - *Tests that don't run in CI.* Only the API unit tests run (on deploy).
    The collections end-to-end test and all of `service/` (scrapers,
    exporter, `test_frontend.py`) only run when someone runs them, while a
    push to `main` deploys the site.
  - *Adding an event depends on the whole manifest.* Each cold Lambda
    downloads and parses all of `events.json`; slower as it grows, and adds
    fail (503) if Pages is down. A small per-event file or the lean manifest
    alone would do.
  - *Rate limits are per IP.* A campus or mobile carrier sharing one IP can
    trip them for a whole group; anyone rotating IPs avoids them.
  - *Times are the adder's time zone.* Your own items' times are entered in
    the browser's zone; collection dates have no zone. Fine for the Bay
    Area, wrong for a visitor planning from elsewhere.
  - *After the plan, nothing.* "Interested" is for deciding; nothing says
    who's actually going once the plan is set (an "I'm in" on the plan,
    reminders); see "Collections → actual plans" below.
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

## Ideas — better social life in SF (not yet planned)

Brainstormed 2026-10-03. Not committed work: pick one, write its spec in
`feature-specs/`, then move it up into the list above. Roughly smallest first.

- **"Meet people" filter** — most events are sit-and-watch; trivia, run
  clubs, classes, mixers, open mics and volunteer shifts are for meeting
  people. Add a tagging dimension (sit-and-watch / some mingling / built for
  meeting people, plus solo-friendly) via a prompt change and a `retag.yml`
  run; a "Meet people" filter or preset and a "Going alone?" hint on rows.
  Small, mostly a tagging problem.
- **Subscribable calendar feeds** — any filtered view as a live calendar
  subscription (`webcal://…/feeds/<view>.ics`) so events land in Google/Apple
  Calendar without opening the site. The export writes static ICS feeds for
  common views (each area, topic, series) plus a "Subscribe" button for the
  current filters. Stays static (no backend). Small–medium.
- **"Become a regular"** — repeated exposure is what turns strangers into
  friends; weekly trivia, run clubs, monthly book clubs and standing open
  mics provide it. Detect recurring series (same source/venue/title pattern
  on a weekly or monthly rhythm, across sources that format titles
  differently), show a "Regulars" view grouped by series ("Every Tue · Trivia
  at Bottom's Up"), and let people follow a series. Medium; the series
  detection is the hard part.
- **Collections → actual plans** — collections already gather events and
  votes; add a "which nights work?" availability poll, "lock in" (the winning
  event becomes calendar invites for everyone) and day-before reminders to
  whoever's going. Builds on the live collections backend. Medium.
- **Weekly "Your weekend" email** — a Thursday email with 5–8 picks matched
  to saved interests and areas, plus anything new from followed series.
  Needs subscriptions (collections backend), sending (SES), unsubscribe and a
  ranking step (Haiku for blurbs). Builds a weekly habit. Medium–large.
- **Lower the bar for small organizers** — the most social events
  (neighbourhood potlucks, small clubs, community classes) rarely use
  ticketing platforms. A web submission form next to the email path, a
  "recurring" option, and an organizer page to keep their series current.
  Medium.
- **Agora Tables** — monthly matching of small groups of strangers (5–6) by
  interest, sent to an event together, with dinner after (Timeleft's model,
  built around events). Needs sign-ups, matching, deposits against no-shows,
  safety/moderation, likely venue partners. The big bet: most impact, most
  work and responsibility.

Suggested order: "Meet people" + calendar feeds first (small, change how the
site is used, no backend), then "Become a regular".

## Ideas — monetization and revenue (not yet planned)

Brainstormed 2026-10-03. The asset is trust ("a free, honest guide to
what's on"), so every option keeps to these **guardrails**: paid placement
is always labelled and never touches ranking, filters or search; no selling
user data (collections, submissions, any future accounts stay private);
anything commercial built on scraped listings credits and links the source,
and reselling content needs opt-in or a partnership.

Near-term (little product work):
- **Ticket affiliate commissions** — referral codes on outbound ticket links
  where a platform has a programme (Eventbrite, Ticketmaster, Tixr, DICE, …).
  Check each platform's terms; commission never affects ordering.
- **Newsletter sponsorship** — once the weekly "Your weekend" email exists,
  one clearly labelled sponsor slot per issue (local SF newsletters prove
  the model).
- **Supporter membership ("Friends of Agora")** — a few dollars a month:
  early access to Tables, perks negotiated with venues, a supporter badge.
  Revenue aligned with the mission.

Medium (needs the organizer path from the social-life ideas):
- **Featured listings** — organizers pay for a marked "Featured" slot,
  capped per day, separate from organic results; Stripe on the web
  submission form.
- **"Claim your venue" pro tools** — a venue/organizer subscription: views,
  clicks, saves and calendar adds for their events; fix details and manage
  recurring series; priority submission review. Builds on stable venue ids.
  Most durable business-to-business revenue.
- **Restaurant/bar referrals around events** — "dinner before the show" near
  the venue via OpenTable/Resy partner links or direct deals; uses venue
  coordinates from the maps work.

Bigger bets:
- **Agora Tables (paid seats)** — a per-seat fee ($15–25) or a cut of a
  set-price dinner on matched small-group outings. Highest margin and most
  aligned with the social-life goal; also the most operations (matching,
  safety, refunds, venue partners).
- **Group-booking deals** — when a collection settles on an event, offer a
  negotiated group ticket for a commission. Builds on collections → plans.
- **Business data feed and embeds** — a clean, deduplicated, tagged,
  geocoded Bay Area events feed or a "What's on near here" widget for
  apartment buildings, hotels/concierge apps, offices and tourism sites.
  Best per-customer revenue, but **needs a legal review first**: event facts
  are generally shareable, but descriptions and images belong to the sources
  and many sources' terms limit commercial reuse — likely facts + links only,
  or opt-in sources.

Suggested order: ticket affiliate links now; newsletter sponsorship and
membership once the weekly email ships; then claim-your-venue pro tools;
Tables as the long-term bet.
