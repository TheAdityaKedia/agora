# Collections: how it works

The reference for Agora's **collections** feature (called *canvases* in the
code, the API and the database). Read this before changing anything in
`canvas/`, `frontend/canvas.html`, `frontend/canvas-client.js` or the
collection parts of `frontend/index.html`.

- Running it, deploying it, the private beta, admin: [`README.md`](README.md)
- Why it was designed this way (the original decisions):
  [`feature-specs/event-canvases.md`](../feature-specs/event-canvases.md)
- Known gaps and planned work: [`future/future-features.md`](../future/future-features.md)
  → "Collections"

## 1. What people can do

A **collection** is a named list of things to do, at its own unguessable link
(`canvas.html?c=<22-char id>`). There are no accounts: **anyone with the link
can edit it**.

| Concept | What it is |
|---|---|
| **Item** | One thing in a collection: an **Agora event** (a copy of a listing, added from the main site) or **your own item** (anything else: dinner, a park, a ride). |
| **Your own items** | Free text + optional time, link, note and a **kind** (🍽️ Food, 🍸 Drinks, 🌳 Outdoors, 🚗 Getting there, 📌 Other). Shown as a tinted dashed card with the kind's icon. Editable; Agora events are not. |
| **👍 Interested** | One per browser per item, shown by name. "Interested", not "I'm in": the collection is for deciding what looks good. |
| **Comments** | Short named comments per item. |
| **The plan** | An ordered list of steps (items), pinned on top. Items have **Add to plan** / **Take out of plan**; steps are reordered by **press, hold and drag** (keyboard: Alt+↑/↓). **Add the plan to your calendar** downloads one `.ics`. |
| **Remove / Restore** | Removing is a soft delete with Undo; the item stays restorable from **Activity** forever. Removing something in the plan, or that others are interested in or commented on, asks first. |
| **Activity** | The last 50 changes ("You added …", "Sam removed … (Fri, Oct 9, 9:45 PM)"), with Restore on removals. |
| **Personal vs shared** | A collection only you have touched is a plain list (no Interested, comments or "Added by"). Those appear once it's shared: someone else opened it, more than one browser has written to it, or you shared it as-is. The plan works in both. |
| **Share** | "Share this collection" (friends edit it with you) or "Share a copy" (they get their own copy; yours stays as it is). **Duplicate** makes a copy for yourself. |
| **Your collections** | `canvas.html` with no `?c=`: every collection this browser opened, split into **Yours** and **Shared with you**, with Hide. |
| **This is mine** | Marks a collection started on another device as yours on this one too (owner devices count as one person). |
| **Default collection** | Per browser: where a future one-tap ☆ Save will add events. |
| **Collecting mode** | On the main page, "Start a collection" (or a collection's **Add events**) turns on a tray, "Adding to <name> · N items", with an add button on every event row. It switches itself off after a day unused. |
| **Private beta** | Hidden unless the browser is a beta member (came in through a `/beta/` link) **and** beta is switched on for this visit (the `/beta/` link does that; members also get a "Beta" switch in the header; the plain address in a new tab starts with it off). See [`README.md`](README.md) → "Private beta". |

## 2. The moving parts

```
 browser                                   AWS (us-east-1)                     GitHub Pages
 ────────────────────────────              ─────────────────────────           ─────────────
 index.html  (collecting mode) ─┐          Lambda Function URL                  events.json
 canvas.html (a collection,     ├─ fetch ─▶ canvas/api/handler.py  ──reads──▶  (the manifest)
              Your collections) │   JSON   ├─ store.py ──▶ DynamoDB table
 canvas-client.js (shared code)─┘          └─ snapshot.py (copies event details
                                                          from events.json)
```

- **One Lambda + one DynamoDB table**, defined in code (AWS CDK) in
  `canvas/infra/`. Two stacks: `AgoraCanvasDev` (deployed from any branch) and
  `AgoraCanvasProd` (deployed from `main`), by
  `.github/workflows/deploy-canvas-api.yml`.
- It is **independent of the rest of Agora**: no scraper code and no Neon. Its
  only link is that, to add an Agora event, it reads the published
  `events.json` and stores a copy of that event's details.
- The **pages are static** (GitHub Pages). They call the API with `fetch`;
  `canvas-client.js` picks the URL (production; dev for `localhost`; or
  `?api=<url>`, remembered).

## 3. Who you are (identity and privacy)

- Each browser makes a random **client id** once (stored in `localStorage`)
  and sends it as the `X-Agora-Client` header. It is how the API knows
  "one vote per browser", which collections are *yours*, and which changes
  were *yours*.
- The API **never returns a client id**. It returns yes/no answers instead:
  `canvas.yours`, `canvas.claimed`, `vote.mine`, `comment.mine`, `log[].mine`,
  `removed[].removed_by_you`.
- **Names are self-declared** ("What's your name?", asked once, the first time
  it matters) and stored per browser. Nothing verifies them.
- **IP addresses** are only used for rate limits, as a salted hash; never
  stored in clear and never logged. The API logs one line per request: method,
  route, status and time; never bodies, names or ids.

## 4. Data model (DynamoDB)

One table (`agora-canvas-prod`; `agora-canvas-dev`), keys `PK` + `SK`, both
strings. Pay-per-request, no secondary indexes, `ttl` attribute for
self-expiring rows, point-in-time recovery on prod (35 days).

| PK | SK | Row | Fields |
|---|---|---|---|
| `C#<id>` | `META` | the collection (one) | `id`, `name`, `note`, `date_from`, `date_to` (`YYYY-MM-DD`), `plan` (ordered list of item ids), `version` (number), `item_count`, `comment_count`, `created_at`, `updated_at`, `created_by_name`, `created_by_client`, `owner_clients` (string set, ≤ 20: "This is mine" browsers). Old collections may have `winner_item_id` instead of `plan` (read as a one-step plan, replaced on the next plan change). |
| `C#<id>` | `ITEM#<itemId>` | an item | `id`, `kind` (`event` / `custom`), `added_by_name`, `added_by_client`, `added_at`. **event:** `event` = {`id`, `title`, `start_time`, `location`, `url`, `image_url`, `sources`} copied when added. **custom:** `title`, `start_time`, `url`, `note`, `category` (`food`, `drinks`, `outdoors`, `travel`, `other`). **removed:** `removed_at`, `removed_by_name`, `removed_by_client`. |
| `C#<id>` | `VOTE#<itemId>#<clientId>` | one 👍 Interested | `name`, `at` |
| `C#<id>` | `CMT#<itemId>#<commentId>` | a comment | `id`, `name`, `client_id`, `text`, `at`, `removed_at` if deleted |
| `L#<id>` | `<ms, 13 digits>#<6 hex>` | an activity entry | `action`, `actor_name`, `client_id`, `at`, and as relevant `item_id`, `item_title`, `name`, `fields` |
| `RL#<ipHash>#<kind>#<bucket>` | `RL` | a rate-limit counter | `count`, `ttl` |

**Item ids.** An Agora event's item id is `ev_<eventId>`, so adding the same
event twice finds the existing item (and restores it if removed). Your own
items get `c_<random>`.

**Activity actions:** `created`, `duplicated`, `renamed`, `edited_note`,
`set_dates`, `added`, `edited`, `removed`, `restored`, `voted`, `commented`,
`deleted_comment`, `added_to_plan`, `removed_from_plan`, `moved_in_plan`; and,
from before plans had steps, `picked_winner` and `cleared_winner`. Un-voting
writes no entry.

**Reading a collection** is two queries: everything under `C#<id>` (META,
items, votes, comments; strongly consistent) and the newest 50 rows of
`L#<id>`. `handler._view` assembles the response: live items (sorted by time,
undated last) with their votes and comments, removed items, the plan
(filtered to live items), activity, and the "people" count that decides
personal vs shared.

**Every write is one transaction**: the change + `META.version += 1` (+
`updated_at`) + usually an activity row. So:

- **Polling is cheap.** An open collection asks `GET …?if_version=N` every
  30 s (2 min after 10 idle minutes; paused in background tabs; immediately
  when the tab comes back). If nothing changed, the API reads only META and
  returns `{unchanged: true}`.
- **Concurrent edits.** Items, votes and comments are separate rows, so two
  people rarely collide. Name, note and dates are last-write-wins (each
  change is logged). The **plan** is one list, so plan changes are
  read-modify-writes conditioned on `version`, retried up to 3 times; a
  removal that takes an item out of the plan does the same.
- **Nothing is hard-deleted by the app.** Removals and deleted comments are
  stamps. Only `scripts/admin.py delete` hard-deletes (for reported content).

## 5. API

Base URL: the Lambda Function URL (in `frontend/canvas-client.js` and the
deploy run's summary). JSON in and out. Every request except `GET` needs the
`X-Agora-Client` header. `actor_name` (optional everywhere) is the name
shown in Activity.

| Method and path | Body | Returns |
|---|---|---|
| `POST /canvases` | `name`, `note?`, `date_from?`, `date_to?` | `201` the full view |
| `GET /canvases/{id}` | — | the full view: `canvas`, `items[]`, `removed[]`, `log[]` |
| `GET /canvases/{id}?if_version=N` | — | `{unchanged: true, version}` or the full view |
| `PATCH /canvases/{id}` | any of `name`, `note`, `date_from`, `date_to` | `{canvas}` |
| `POST /canvases/{id}/duplicate` | `name?` | `201` the new collection's full view |
| `POST` / `DELETE /canvases/{id}/claim` | — | the full view ("This is mine" / "Not mine") |
| `POST /canvases/{id}/plan` | `op` (`add`, `remove`, `move`), `item_id`, `to?` (position, 0 first) | `{plan: [itemId…]}` |
| `POST /canvases/{id}/items` | `event_id`, or `custom: {title, start_time?, url?, note?, category?}` | `201 {item, created: true}`; `200 … created: false` if already there |
| `PATCH /canvases/{id}/items/{itemId}` | `custom: {…}`: only the fields that change; empty clears one (not the title) | `{item}`; your own items only |
| `DELETE /canvases/{id}/items/{itemId}` | — | soft delete (also leaves the plan) |
| `POST /canvases/{id}/items/{itemId}/restore` | — | restore (not back into the plan) |
| `PUT` / `DELETE /canvases/{id}/items/{itemId}/vote` | `name` (PUT) | Interested / undo |
| `POST /canvases/{id}/items/{itemId}/comments` | `name`, `text` | `201 {comment}` |
| `DELETE /canvases/{id}/items/{itemId}/comments/{commentId}` | — | soft delete |

**Where a plan step goes when added:** before the first step that starts
later, so a dated plan starts in time order; undated steps go last.

**Errors:** `400` invalid input (message says which field), `404` unknown
collection/item/event (or an event that has left the manifest), `409` a cap
or a conflict that survived retries, `413` body over 16 KB, `429` rate limit
(with `Retry-After`), `503` the manifest couldn't be fetched.

**Limits:** 100 live items, 20 plan steps, 1,000 comments per collection;
20 owner devices. Lengths: collection name 80, note 1,000, person name 40,
comment 500, own-item title 120 and note 500, URL 500. Rate limits per IP:
10 new collections (incl. copies) per hour, 120 writes per 10 minutes. Text
is cleaned of control and bidi-override characters; links must be http(s).

**CORS:** only the origins in the stack's `ALLOWED_ORIGINS` (the Pages site;
`localhost` on dev).

**Event copies:** `snapshot.py` keeps the parsed `events.json` per warm
Lambda for 10 minutes and re-fetches at most once a minute when asked for an
id it doesn't know (so a just-published event can be added soon after a
data refresh). The copy is never updated afterwards (see the gaps below).

## 6. The frontend

| File | Role |
|---|---|
| `frontend/canvas-client.js` | Shared by both pages, as `window.AgoraCanvas`: API calls, client id, name prompt, dialogs (form, choice), toasts, share sheet, the browser's lists (`mine`, `shared`, default, active collection), the beta gate. |
| `frontend/canvas.html` | One collection (`?c=`), or "Your collections" (no `?c=`). Renders everything from the `GET` view; every action is a write, then a refresh. |
| `frontend/index.html` | Collecting mode: the tray and the per-row add buttons (`canvasMode`, `canvasAddHtml`, `toggleInCanvas`). |
| `frontend/beta/` | `/beta/` entry pages: set the beta flag and redirect. Keep them forever: beta-era links point there. |

**What the browser stores** (`localStorage`, keys `agora.canvas.*`):
`client` (the id), `name`, `mine` (collections opened here, ≤ 100, with
`yours`), `shared` (shared as-is from here), `default`, `active` (collecting
mode, expires after 24 h unused), `api` (override), `beta` (membership);
and in `sessionStorage`, `agora.beta.on` (beta switched on this visit). Clearing site
data loses the list, the name and the default, not the collections.

**Page behaviour worth knowing:** polling never re-renders while you are
typing in a field or dragging a plan step (it waits); a step being dragged is
moved on screen first and saved after; while the beta gate is on, collection
pages keep `?beta=1` in the address bar so a copied link works.

## 7. Tests and checks

| What | How | In CI? |
|---|---|---|
| API (routes, validation, store, plan concurrency, privacy) | `cd canvas && python -m pytest` (moto, no AWS) | yes, before every deploy |
| Infrastructure (CDK assertions) | same suite, `tests/test_infra.py` | yes |
| After deploy | `scripts/smoke.py` against the dev URL | yes, on branch deploys |
| Pages end to end (three browsers: beta gate, collecting mode, sharing, Interested, comments, plan drag, edit, remove/restore, duplicate, claim/default) | `CHROMIUM_PATH=… python canvas/scripts/e2e_frontend.py [--shots DIR]` | **no**, run it by hand |

## 8. Changing things: where to look

- **A new field on your own items:** `handler.CUSTOM_KEYS` and
  `_custom_fields` (validation), `_item_out` (output), `customDialog` and
  `itemHtml` in `canvas.html`, a test, and the tables above.
- **A new activity action:** write it with `store._log_row(…)` inside the
  write's transaction; add its sentence to `logText` in `canvas.html`.
- **A new endpoint:** a function in `handler.py` + a line in `ROUTES`; store
  work in `store.py` as one `_transact([...])` that includes `_bump(...)`.
- **Anything about the event copy:** `snapshot.SNAPSHOT_FIELDS`.
- **Keep this file current** when you change behaviour, the schema or the
  API; it is the reference the others link to.
