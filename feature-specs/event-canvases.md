# Collections (event canvases) — save events, or plan with friends

> **Naming:** users see **collections** ("Start a collection", "Your
> collections"); the code, API and table still say **canvas**. This spec
> started as "plan a hangout with friends" and was widened (2026-10-03, with
> the owner) to also cover saving events for yourself — see
> "Collections for yourself" below.

**Status: phase 1 (backend) shipped in `canvas/` (prod + dev live — see
`canvas/README.md`); phases 2–3 (the pages) built on a branch; phase 4 docs
partly done.** Decisions below were settled with the owner
(2026-10-02). Open items are small and listed at the end.

## Collections for yourself (added 2026-10-03)

A collection is just a named list of events with a link, so it serves two
uses with one model:

- **Social parts appear only once a collection is shared.** A collection you
  started and nobody else has touched is a plain list (events, Remove,
  Duplicate). Votes ("Interested"), comments and "Added by"
  box appear when any of: you opened someone else's link (`canvas.yours` is
  false), more than one browser has done something on it (`canvas.people >
  1`, counted server-side from creator / item adders / voters / commenters —
  ids never leave the server), or you chose **Share this collection** in this
  browser (a local flag). No new stored state.
- **Names only when others will see them.** `actor_name` is optional on every
  write; curating never prompts. The name prompt comes with sharing as-is,
  voting and commenting (`vote.name` and `comment.name` stay required).
  Unnamed log rows read "You" on your own collection, "Someone" elsewhere.
- **Share** on a collection only you have used asks: **Share this
  collection** (friends add / vote / comment on it) or **Share a copy** (a
  duplicate for them; yours stays as it is). Already-shared collections go
  straight to the share sheet. The copy's link is shared from a second tap
  ("Your copy is ready"), because browsers only open the share sheet right
  after a tap.
- **Duplicate** (any collection): a new collection with the same name (or a
  new one), note, dates and live items; no votes, comments, plan or log.
  Event items keep their `ev_<event_id>` ids; custom items get new ids;
  "Added by" names are kept as provenance.
- **Your collections** (`canvas.html` without `?c=`, linked from the main
  page header and every collection): every collection this browser has
  started or opened, in two sections, **Yours** and **Shared with you** (from
  `canvas.yours` when it was last opened), newest first, up to 100; the
  default is pinned first; copies you made to share are marked. Per browser
  until accounts exist; Hide only removes the entry from the list.
- **This is mine** (multi-device, added 2026-10-03): a collection started on
  your phone is "Shared with you" on your PC. Tapping **This is mine** (on the
  collection, or its row) adds that browser to `META.owner_clients` (a string
  set, ≤ 20): `yours` becomes true there, and all owner devices count as
  **one** person in `people`, so adding from both keeps it a personal list.
  **Not mine** undoes it (the creating browser can't be removed). Anyone with
  the link could claim; it changes only how the collection is shown, never
  what anyone can do. Accounts / "link this device" would replace this.
- **Make this my default** (per browser, `localStorage`): the collection
  ☆ Save will add to; pick the same one on each device. Marked "★ Your
  default" on the collection and pinned in Your collections.
- **Private beta** (added 2026-10-03): collections launch behind
  `BETA_GATE` in `canvas-client.js`. `frontend/beta/index.html` and
  `frontend/beta/canvas.html` set a per-browser flag and redirect to the real
  page (`?beta=1`); without the flag the main page shows no collections UI
  and `canvas.html` shows a "private beta" notice. Links made during the beta
  use `…/beta/canvas.html?c=`, so recipients get in; collection pages (and
  canvas mode) also keep `?beta=1` in the address bar, so a link copied
  from there works as well. Launch = flip the
  constant; the `/beta/` pages stay forever as redirects so beta-era links
  keep working (checked by flipping it in a scratch copy). See
  `canvas/README.md` → "Private beta".
- Wording: "Start a collection" (tooltip: "Save events that interest you,
  or plan with friends"), "Your collections", canvas-mode tray "Adding to
  <name> · N items".

## The problem

Two friends text "let's catch up". Today Agora can show them *what's on*, but
the deciding happens elsewhere: screenshots, pasted links, "which one was the
jazz thing?". We want one link that holds a shortlist and lets the group say
which options work for them.

The flow:

1. Adi creates a canvas ("Adi & Sam hangout", optionally Oct 10–12).
2. Adi browses Agora as usual; every event card has **+ Add**. A tray shows
   the canvas filling up.
3. Adi texts the canvas link to Sam.
4. Sam opens it on a phone, marks the two that look good 👍 **Interested**,
   adds dinner before (one of their own items), and comments "free after 7".
5. They **Add to plan** the show and the dinner; the plan pins to the top as
   numbered steps (moved with ↑/↓), with an add-to-calendar button.

## Goal & non-goals

**In v1:**
- Create a named canvas with an optional date range and a free-text note.
- Add Agora events (from the main site, in "active canvas" mode) and custom
  items (free text + optional link + optional time) not in Agora.
- **Anyone with the link can edit**: add, remove, restore, rename, edit the
  note, build and reorder the plan. One link, no roles.
- Named 👍 **Interested** votes: one per browser per item, shown as names.
  ("Interested", not "I'm in": the collection is for deciding what looks
  good; you're in once the plan is settled.)
- Short named comments per item.
- Soft delete + restore, an activity log, per-IP rate limits.
- "My canvases" remembered in the browser.
- Canvases are **kept forever**.

**Not in v1 (deliberately):**
- Accounts / login. (Designed for, see "Accounts later".)
- Real-time sync. Polling every ~30s is enough for a friends list.
- Per-canvas link previews (iMessage/WhatsApp unfurls). Canvas links show the
  generic Agora card; keeps the site fully static.
- Public listing, search, or discovery of canvases. Canvases are *unlisted*:
  the unguessable link is the only way in.
- Moderation tooling beyond an operator delete script.
- Notifications ("Sam voted").

## Design decisions (with rationale)

### 1. Agora gets its first live backend: AWS Lambda + DynamoDB
The site is static GitHub Pages and the manifest is rebuilt daily; shared,
mutable, visitor-written state can't live there. Options considered:

- **Neon + small API host** — rejected: canvas pages poll, which would keep
  the Neon compute awake and burn the free-tier compute hours the scrape jobs
  depend on. Neon also remains the *scrape* working store; mixing in durable
  user data muddies "the DB is working state" (CLAUDE.md).
- **Cloudflare Workers + D1 / Supabase** — viable; rejected to avoid a new
  vendor. The owner already has an AWS account (Bedrock).
- **Chosen: one Lambda function (Python 3.12) behind a Lambda Function URL,
  one DynamoDB table.** No API Gateway (Function URLs are free). Python
  matches the rest of the repo. Expected cost at friends-scale: ~$0 (Lambda
  and DynamoDB always-free tiers). Guard rails in §8.

The static site stays on GitHub Pages; it just calls the API with `fetch`.

### 2. A separate, light `canvas.html` page
Opening a shared canvas on a phone must not download the 6.8 MB
`events.json` and build the search index. So the canvas view is a new
self-contained page, `frontend/canvas.html?c=<id>`, that fetches only the
canvas from the API. It renders entirely from the canvas data (see §3), never
from the manifest.

The main `index.html` gains "active canvas" mode for *adding* (§6).

### 3. Event items are server-built snapshots
Agora drops past events from the manifest, and event IDs can change when a
source is wiped and re-scraped or when dedup merges rows. A canvas kept
forever can't depend on either. So each event item stores a **snapshot**:
`event_id, title, start_time, location, url, image_url, sources`.

The **server** builds the snapshot, not the browser: the client sends only
`event_id`, and the Lambda looks it up in the live `events.json` (fetched from
Pages, cached in memory ~10 min, re-fetched once on a miss). This means
anyone-can-edit can't be used to plant a fake "Agora event" with an arbitrary
image or link — event items are always real Agora data. Custom items are the
only free-form content, and they carry no image.

### 4. Identity without accounts: a browser client id + a self-chosen name
- On first visit the browser generates a random `client_id` (UUID, in
  `localStorage`) and sends it as `X-Agora-Client` on every request. It is
  **not** auth — it dedups votes ("one 👍 per browser per item"), lets you
  un-vote / edit your own name, and records who created a canvas so a future
  account can claim it.
- The first write action (vote, add, comment, create) asks "What's your
  name?" once; stored in `localStorage`, changeable from the canvas menu.
  Names are visible to anyone with the link — the prompt says so.
- Both are spoofable. That's acceptable for a friends list; the link is the
  real boundary.

### 5. Concurrency: per-row writes + a canvas version
Items, votes and comments are separate rows, so two people adding/voting at
once never conflict. Canvas-level fields (name, note, dates) are
last-write-wins and every change lands in the activity log. The plan is
changed one step at a time (add / remove / move), each a read-modify-write
conditioned on `META.version` and retried on conflict, so two people editing
it at once don't overwrite each other. Every write also
bumps `META.version` in the same DynamoDB transaction; polling compares
versions so an unchanged canvas costs one tiny read.

### 6. Adding events: "active canvas" mode on the main site
- Entry points: **Plan with friends** button in the header (create → name +
  optional dates) and **Browse events to add** on `canvas.html`, which links
  to `index.html?canvas=<id>`.
- While active (URL `?canvas=<id>`, mirrored to `localStorage` so it survives
  navigation; the remembered canvas switches off 24 hours after it was last
  used, so a later visit to browse isn't still adding to an old plan — a
  `?canvas=` link always turns it back on): each event card shows **+ Add** / **✓ Added**; a sticky bottom
  tray shows "Adi & Sam hangout · 4 · View · Done". If the canvas has a date
  range, activation applies it as the date filter (user can still change it).
- "Added" is matched by `event_id`, falling back to `(title, start_time)` so
  an event whose ID churned isn't added twice.
- `canvas` joins the URL state in `writeStateToUrl` / `readStateFromUrl`.
  Mind the IIFE temporal-dead-zone rule in CLAUDE.md: any `const` that
  `readStateFromUrl` touches for canvas mode must be declared before it runs.

### 7. Canvas page UX (mobile-first)
- Header: name (tap to edit), date range, note (tap to edit), **Share**
  (`navigator.share` on phones, copy-link fallback), **Browse events to add**.
- **The plan** (if any steps) pinned on top: numbered steps in the group's
  order, each with time (just the time when every dated step is on one day,
  which is said once), title (your own items with their kind icon), place,
  a Google Calendar link and **Take out**. **Press and hold a step, then drag
  it into place**: it lifts and follows the finger, the other steps slide out
  of its way, and it settles into the gap on release (touch: hold ~0.3 s
  first, so a quick swipe still scrolls; mouse: press and move; keyboard:
  focus a step, Alt+↑/↓). The plan is in personal collections too: a solo
  itinerary. **Add the plan
  to your calendar** downloads one `.ics` with every dated step. A newly
  added step goes before the first step that starts later (undated: last),
  so a dated plan starts in time order; people can then move steps.
- Items sorted by start time; undated custom items last. Each item: snapshot
  card, **👍 Interested N** with names ("Adi, Sam"), comment count
  (expands), **Add to plan** / **Take out of plan** (an "In the plan" badge
  when it's in), Remove.
- Your own items can be edited (**Edit**: kind, what, when, link, note;
  `PATCH …/items/{itemId}` with the changed `custom` fields, empty clears
  one); Agora events can't (they're snapshots).
- Your own items (not Agora events) look different: a tinted card with a
  dashed edge, an icon for their kind (🍽️ Food, 🍸 Drinks, 🌳 Outdoors,
  🚗 Getting there, 📌 Other; picked with chips in **Add your own**) in the
  image's place, and a pill: "Added by <name>" once shared, else "Your own".
- Remove = soft delete with an "Undo" toast. On a shared collection it asks
  for your name first (once), so others see who removed what. Removing the
  plan, or an item others have 👍'd or commented on, asks to confirm first
  ("It’s in the plan…", "Sam and Jo are interested."); your own
  votes and comments alone don't.
- Collapsed "Activity · N removed" list (last 50 entries), which also holds
  removals: the latest removal of a still-removed item carries **Restore**
  (removals older than the 50 are appended from the removed items, so
  nothing becomes unrestorable). Each entry names the event's date and time,
  which tells apart showings with the same title. Entries read "You" for
  this browser's own changes, else the name, else "Someone": log rows and
  removed items store the actor's client id, and the API returns only a
  `mine` / `removed_by_you` yes/no (never the id). Rows from before that was
  stored fall back to "You" on your own collection.
- Items whose time has passed render dimmed with "Past"; the canvas stays
  fully editable.
- Polling: every 30s while the tab is visible, slowing to every 2 min after
  10 min of no interaction, paused when hidden; immediate refetch after your
  own write.
- All user text is escaped. Item links (event URLs, custom-item links) are
  rendered only for `http(s)`, with `rel="noopener nofollow ugc"`; comments
  stay plain text (no linkifying) in v1.
- The custom item's link field is plain text with a URL keyboard, not
  `type=url` (the browser silently refuses "nopasf.com"); a missing scheme
  becomes `https://`.
- Static Agora `og:` tags only (no per-canvas previews, per non-goals).

### 8. Abuse and cost guard rails (no moderation)
- Canvas ids: 128 random bits, base64url (22 chars). Unguessable and unlisted.
- Rate limits per salted IP hash: 10 canvas creates / hour; 120 writes /
  10 min. Limit counters are short-lived DynamoDB rows with TTL.
- Size caps: canvas name ≤ 80 chars, note ≤ 1,000, display name ≤ 40, comment
  ≤ 500, custom item title ≤ 120; ≤ 100 live items, ≤ 1,000 comments per
  canvas. The activity log lives in its own partition and only the latest 50
  entries are ever read, so it needs no cap (rate limits bound its growth).
- Lambda reserved concurrency capped (e.g. 10) so a flood can't run up cost.
- AWS Budgets alert at $5/month to the owner's email.
- Operator script `canvas/scripts/admin.py delete <id>` hard-deletes a canvas
  if one is reported. No in-app report button in v1.
- Privacy: no raw IPs stored; never log request bodies or names; CloudWatch
  log retention 14 days.

### 9. Accounts later
Every canvas stores `created_by_client`, and every write stores the
`client_id`. When accounts arrive: login → the browser sends its `client_id`
→ the server attaches canvases it created (add a GSI on `created_by_client`
then; it backfills automatically). Nothing in v1 has to be migrated.

## Data model — one DynamoDB table `agora-canvas-<stage>`

Single-table design; one `Query` on `C#<id>` returns the whole canvas, one
more (newest 50) on `L#<id>` returns the log.

| PK | SK | Attributes |
|----|----|-----------|
| `C#<canvasId>` | `META` | `name, note, date_from, date_to, plan (list of item ids), version, created_at, created_by_client, created_by_name, updated_at` |
| `C#<canvasId>` | `ITEM#<itemId>` | `kind` (`event`\|`custom`), `snapshot` (event) or `title, url, start_time, note` (custom), `added_by_name, added_by_client, added_at, removed_at?, removed_by_name?` |
| `C#<canvasId>` | `VOTE#<itemId>#<clientId>` | `name, at` |
| `C#<canvasId>` | `CMT#<itemId>#<commentId>` | `name, client_id, text, at, removed_at?` |
| `L#<canvasId>` | `<ms>#<rand>` | `actor_name, action, item_id?, item_title?, fields?, at` |
| `RL#<ipHash>#<kind>#<bucket>` | `RL` | `count, ttl` (TTL-expired) |

Event items have the deterministic id `ev_<event_id>`, so an event can
appear once per canvas: adding an already-present `event_id` returns the
existing item (and restores it if soft-deleted). Custom items get
`c_<random>`. `META` also carries `item_count` / `comment_count` for the caps.

Capacity: on-demand for both stages. (Provisioned capacity inside the
always-free 25 RCU/WCU was considered, but a strongly-consistent read of a
large canvas can burst past 10 RCU and throttle; on-demand at this scale is
pennies a month and never throttles.)

## API (Lambda Function URL, JSON)

Writes must send `X-Agora-Client` (reads may, to get `mine` / `you_voted`
flags). Responses never include anyone's client id — that would make it
trivial to delete other people's votes. CORS allows
`https://theadityakedia.github.io`, plus `http://localhost:*` on the dev stack
only.

| Method & path | Body | Result |
|---|---|---|
| `POST /canvases` | `{name, actor_name?, note?, date_from?, date_to?}` | `201 {canvas, items:[], …}` |
| `POST` / `DELETE /canvases/{id}/claim` | — | count / stop counting this browser as an owner device; the full view (`canvas.yours`, `canvas.claimed`) |
| `POST /canvases/{id}/duplicate` | `{name?, actor_name?}` | `201` the new canvas's full view (counts against the create rate limit) |
| `GET /canvases/{id}` | — | `{canvas (incl. version, yours, people), items[] (with votes[], you_voted, comments[]), removed[], log[]}` |
| `GET /canvases/{id}?if_version=N` | — | `200 {unchanged:true, version}` if unchanged (reads only `META`); else the full view. Not a `304`: browsers handle an unsolicited 304 inconsistently in `fetch`. |
| `PATCH /canvases/{id}` | any of `{name, note, date_from, date_to}` + `actor_name` | `{canvas}` |
| `POST /canvases/{id}/plan` | `{op: add\|remove\|move, item_id, to?}` + `actor_name` | `{plan: [itemId…]}` (`to` = new position, 0 first; add/remove of what's already there is a no-op; at most 20 steps) |
| `POST /canvases/{id}/items` | `{event_id}` or `{custom:{title, url?, start_time?, note?, category?}}` (category: food, drinks, outdoors, travel, other) + `actor_name` | `201 {item, created:true}`; `200 {item, created:false}` if the event was already there |
| `PATCH /canvases/{id}/items/{itemId}` | `{custom:{title?, url?, start_time?, note?, category?}}` + `actor_name` | `{item}`; your own items only (an empty value clears a field; not the title) |
| `DELETE /canvases/{id}/items/{itemId}` | `{actor_name}` | soft delete |
| `POST /canvases/{id}/items/{itemId}/restore` | `{actor_name}` | restore |
| `PUT /canvases/{id}/items/{itemId}/vote` | `{name}` | upsert this client's vote |
| `DELETE /canvases/{id}/items/{itemId}/vote` | — | remove this client's vote |
| `POST /canvases/{id}/items/{itemId}/comments` | `{name, text}` | `201 {comment}` |
| `DELETE /canvases/{id}/items/{itemId}/comments/{commentId}` | `{actor_name}` | soft delete (any editor) |

`actor_name` is optional everywhere (see "Collections for yourself").

Errors: `400` validation, `404` unknown canvas/item/event, `409` item cap,
`413` body over 16 KB, `429` rate limit (with `Retry-After`), `503` manifest
unreachable. Every write is one `TransactWriteItems`: the change +
`META.version += 1` + a log row (un-voting writes no log row). Removing an
item takes it out of the plan in the same write (restoring doesn't put it
back). `canvas.plan` is the ordered list of live item ids; collections from
before multi-step plans had one `winner_item_id`, read as a one-step plan
and replaced by `plan` on the next plan change.

## Code layout

```
canvas/                     # new; independent of service/ (no scraper deps)
  api/handler.py            # routing, validation, DynamoDB access
  api/snapshot.py           # events.json fetch/cache → snapshot
  infra/                    # AWS CDK (Python): API stacks (dev/prod) + CI deploy-role stack
  scripts/local_server.py   # run the API locally (moto or a real table)
  scripts/smoke.py          # post-deploy create → add → vote → read
  scripts/admin.py          # operator delete / inspect
  tests/                    # pytest + moto
frontend/canvas.html        # the canvas (?c=<id>), or "Your canvases" without ?c
frontend/canvas-client.js   # shared by both pages: API, client id, name, dialogs, toast
frontend/index.html         # canvas mode (+ Add, tray), "Plan with friends"
canvas/scripts/e2e_frontend.py  # headless-Chromium run of the whole flow
.github/workflows/deploy-canvas-api.yml
```

Shared client code (client id, name prompt, API wrapper, my-canvases,
dialogs, toast) lives in one plain script, `frontend/canvas-client.js`, that
both pages load — still no build step, and no copies to keep in step. It
holds the prod and dev API URLs; pages on `localhost` use dev (or
`?api=<url>`, remembered).

**Deploy:** infrastructure is code (AWS CDK, Python, `canvas/infra/`), so an
infra change ships like any other change. `deploy-canvas-api.yml` runs
`cdk deploy` on pushes touching `canvas/` — branch → `AgoraCanvasDev`,
`main` → `AgoraCanvasProd` + `AgoraCanvasCi` (the deploy role itself). AWS
auth via GitHub OIDC (no long-lived keys); the only manual step ever is the
one-time `cdk bootstrap` + first `cdk deploy AgoraCanvasCi` (CDK chosen over
SAM YAML + a hand-uploaded bootstrap template at the owner's request). The prod
Function URL is a constant in both pages; on `localhost` an `?api=` override
points at dev or a local server.

## Phases

1. **Backend** — `canvas/` handler + CDK stacks + moto tests; deploy the
   `dev` stack from the branch; smoke-test with curl.
2. **Canvas page** — `canvas.html`: view, vote, comments, note, plan +
   calendar, custom items, remove/restore, activity, polling.
3. **Main-site integration** — Plan-with-friends create flow, active-canvas
   mode (+ Add, tray, date filter, URL state), my-canvases menu.
4. **Launch** — prod stack, budget alert, docs: README section ("Event
   canvases": architecture, deploy, admin delete), CLAUDE.md layout/router
   lines, delete this spec's `future-features.md` entry.

Phases 2–3 ship together in one PR (the flow needs both); phase 1 can land
first since nothing calls it.

## Testing plan

- **Handler unit tests (pytest + moto):** validation and caps; canvas id
  shape; version bumps on every write and `unchanged` on `if_version`; idempotent
  event add + restore-on-re-add; soft delete/restore; one vote per client,
  un-vote; comment soft delete; rate-limit `429`; CORS origin allow/deny;
  snapshot built from a trimmed `events.json` fixture, unknown `event_id` →
  `404`, cache refetch on miss.
- **Frontend (headless Chromium via `service/.venv` Playwright, per
  CLAUDE.md):** serve `frontend/` with `python3 -m http.server`, run the
  handler behind a tiny local HTTP adapter with moto. Script the whole story:
  create canvas → add 2 events in canvas mode → open `canvas.html` → name
  prompt → vote → second browser context votes as another name → reload shows
  both names; remove + undo; build and reorder the plan. Fail on any `pageerror`. Also load
  `index.html?canvas=<id>&q=jazz` to catch TDZ/"stuck on Loading…" regressions.
- **Mobile viewport** (390×844) screenshot check of `canvas.html`.
- **Post-deploy smoke** against `dev`: create → add → vote → get.

## Open (small) decisions

- Custom domain for the API (e.g. `api.<domain>`) vs the raw Function URL.
  Raw URL is fine until Agora has its own domain.
- Whether `canvas.html` should later offer "suggested events" from the
  canvas's date range (needs a slice of the manifest; out of v1).
