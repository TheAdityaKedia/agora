# Event canvases — plan a hangout with friends

**Status: designed, not built.** Decisions below were settled with the owner
(2026-10-02). Open items are small and listed at the end.

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
4. Sam opens it on a phone, 👍s the two that work ("Sam ✓"), adds one more,
   and comments "free after 7".
5. Someone marks the winner; it pins to the top with an add-to-calendar link.

## Goal & non-goals

**In v1:**
- Create a named canvas with an optional date range and a free-text note.
- Add Agora events (from the main site, in "active canvas" mode) and custom
  items (free text + optional link + optional time) not in Agora.
- **Anyone with the link can edit**: add, remove, restore, rename, edit the
  note, pick the winner. One link, no roles.
- Named 👍 votes: one per browser per item, shown as names.
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
once never conflict. Canvas-level fields (name, note, dates, winner) are
last-write-wins and every change lands in the activity log. Every write also
bumps `META.version` in the same DynamoDB transaction; polling compares
versions so an unchanged canvas costs one tiny read.

### 6. Adding events: "active canvas" mode on the main site
- Entry points: **Plan with friends** button in the header (create → name +
  optional dates) and **Browse events to add** on `canvas.html`, which links
  to `index.html?canvas=<id>`.
- While active (URL `?canvas=<id>`, mirrored to `localStorage` so it survives
  navigation): each event card shows **+ Add** / **✓ Added**; a sticky bottom
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
- Winner (if set) pinned on top with **Add to calendar** (client-generated
  `.ics` + Google Calendar link).
- Items sorted by start time; undated custom items last. Each item: snapshot
  card, **👍 N** with names ("Adi, Sam"), comment count (expands), overflow
  menu (Mark as winner, Remove).
- Remove = soft delete with an "Undo" toast; a collapsed "Removed (N)"
  section allows restore.
- Collapsed "Activity" list (last 50 entries).
- Items whose time has passed render dimmed with "Past"; the canvas stays
  fully editable.
- Polling: every 30s while the tab is visible, slowing to every 2 min after
  10 min of no interaction, paused when hidden; immediate refetch after your
  own write.
- All user text is escaped; custom-item and comment links are linkified only
  for `http(s)` with `rel="noopener nofollow ugc"`.
- Static Agora `og:` tags only (no per-canvas previews, per non-goals).

### 8. Abuse and cost guard rails (no moderation)
- Canvas ids: 128 random bits, base64url (22 chars). Unguessable and unlisted.
- Rate limits per salted IP hash: 10 canvas creates / hour; 120 writes /
  10 min. Limit counters are short-lived DynamoDB rows with TTL.
- Size caps: canvas name ≤ 80 chars, note ≤ 1,000, display name ≤ 40, comment
  ≤ 500, custom item title ≤ 120; ≤ 100 live items, ≤ 1,000 comments, ≤ 2,000
  log entries per canvas (oldest log entries trimmed).
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

Single-table design; one `Query` on `PK` returns the whole canvas.

| PK | SK | Attributes |
|----|----|-----------|
| `C#<canvasId>` | `META` | `name, note, date_from, date_to, winner_item_id, version, created_at, created_by_client, created_by_name, updated_at` |
| `C#<canvasId>` | `ITEM#<itemId>` | `kind` (`event`\|`custom`), `snapshot` (event) or `title, url, start_time, note` (custom), `added_by_name, added_by_client, added_at, removed_at?, removed_by_name?` |
| `C#<canvasId>` | `VOTE#<itemId>#<clientId>` | `name, at` |
| `C#<canvasId>` | `CMT#<itemId>#<ts>#<rand>` | `name, client_id, text, at, removed_at?` |
| `C#<canvasId>` | `LOG#<ts>#<rand>` | `actor_name, action, item_title?` |
| `RL#<ipHash>#<bucket>` | `RL` | `count, ttl` (TTL-expired) |

`itemId` is random (8 bytes base64url). An event can appear once per canvas:
adding an already-present `event_id` returns the existing item (and restores
it if soft-deleted).

Capacity: provisioned 10 RCU / 10 WCU (inside the always-free 25/25) for
`prod`; on-demand for `dev`. If prod ever throttles, switch to on-demand
(pennies at this scale).

## API (Lambda Function URL, JSON)

All requests send `X-Agora-Client`. CORS allows only
`https://theadityakedia.github.io` and `http://localhost:*`.

| Method & path | Body | Result |
|---|---|---|
| `POST /canvases` | `{name, name_of_creator, note?, date_from?, date_to?}` | `201 {canvas}` |
| `GET /canvases/{id}` | — | `{canvas, items[] (with votes[], comments[]), removed[], log[], version}` |
| `GET /canvases/{id}?if_version=N` | — | `304` if unchanged (reads only `META`) |
| `PATCH /canvases/{id}` | any of `{name, note, date_from, date_to, winner_item_id}` + `actor_name` | `{canvas}` |
| `POST /canvases/{id}/items` | `{event_id}` or `{custom:{title, url?, start_time?, note?}}` + `actor_name` | `201 {item}` |
| `DELETE /canvases/{id}/items/{itemId}` | `{actor_name}` | soft delete |
| `POST /canvases/{id}/items/{itemId}/restore` | `{actor_name}` | restore |
| `PUT /canvases/{id}/items/{itemId}/vote` | `{name}` | upsert this client's vote |
| `DELETE /canvases/{id}/items/{itemId}/vote` | — | remove this client's vote |
| `POST /canvases/{id}/items/{itemId}/comments` | `{name, text}` | `201 {comment}` |
| `DELETE /canvases/{id}/comments/{commentSk}` | `{actor_name}` | soft delete (any editor) |

Errors: `400` validation, `404` unknown canvas/item/event, `409` item cap,
`429` rate limit (with `Retry-After`). Every write is one
`TransactWriteItems`: the change + `META.version += 1` + a `LOG#` row.

## Code layout

```
canvas/                     # new; independent of service/ (no scraper deps)
  api/handler.py            # routing, validation, DynamoDB access
  api/snapshot.py           # events.json fetch/cache → snapshot
  template.yaml             # AWS SAM: function + URL, table, TTL, budget, log group
  scripts/admin.py          # operator delete / inspect
  tests/                    # pytest + moto
frontend/canvas.html        # new page (inline CSS/JS, no build, like index.html)
frontend/index.html         # canvas mode, create flow, my-canvases menu
.github/workflows/deploy-canvas-api.yml
```

Shared client code (client id, name prompt, API wrapper, my-canvases) is
small enough to duplicate inline in both pages rather than introduce a build
step; keep the two copies identical and note it in a comment.

**Deploy:** `deploy-canvas-api.yml` runs `sam deploy` on pushes touching
`canvas/` — branch → `dev` stack, `main` → `prod` stack. AWS auth via GitHub
OIDC → an IAM role scoped to the two stacks (no long-lived keys). The prod
Function URL is a constant in both pages; on `localhost` an `?api=` override
points at dev or a local server.

## Phases

1. **Backend** — `canvas/` handler + SAM template + moto tests; deploy the
   `dev` stack from the branch; smoke-test with curl.
2. **Canvas page** — `canvas.html`: view, vote, comments, note, winner +
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
  shape; version bumps on every write and `304` on `if_version`; idempotent
  event add + restore-on-re-add; soft delete/restore; one vote per client,
  un-vote; comment soft delete; rate-limit `429`; CORS origin allow/deny;
  snapshot built from a trimmed `events.json` fixture, unknown `event_id` →
  `404`, cache refetch on miss.
- **Frontend (headless Chromium via `service/.venv` Playwright, per
  CLAUDE.md):** serve `frontend/` with `python3 -m http.server`, run the
  handler behind a tiny local HTTP adapter with moto. Script the whole story:
  create canvas → add 2 events in canvas mode → open `canvas.html` → name
  prompt → vote → second browser context votes as another name → reload shows
  both names; remove + undo; mark winner. Fail on any `pageerror`. Also load
  `index.html?canvas=<id>&q=jazz` to catch TDZ/"stuck on Loading…" regressions.
- **Mobile viewport** (390×844) screenshot check of `canvas.html`.
- **Post-deploy smoke** against `dev`: create → add → vote → get.

## Open (small) decisions

- Custom domain for the API (e.g. `api.<domain>`) vs the raw Function URL.
  Raw URL is fine until Agora has its own domain.
- Whether removing the *winner* clears it (proposed: yes, and log it).
- Whether `canvas.html` should later offer "suggested events" from the
  canvas's date range (needs a slice of the manifest; out of v1).
