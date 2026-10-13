# Collection sharing: show it, or plan it together

**Status: spec, not started (2026-10-13). Must ship before collections leave
the beta.** Decisions below were settled with the owner on 2026-10-13. UI
mocks: the "Collections: lists and plans" design canvas (the Hybrid row).
How collections work today: [`canvas/HOW-IT-WORKS.md`](../canvas/HOW-IT-WORKS.md).

## Why

Today a collection has one link, anyone who has it can edit, and planning
(👍 Interested + The plan) appears as soon as anyone else is involved. That
fits friends planning a night out. It doesn't fit two other uses:

- **Curators** (local influencers, friends with taste): a collection made to
  *show* followers what's worth going to. Followers must not be able to
  change it, and planning UI on it is a distraction.
- **Personal lists**: a collection just for you, where a note on each event
  ("sit on the left") matters more than votes.

## The model

**Comments are on in every collection**, personal ones included (they work
as notes to yourself). **Planning** (Interested + The plan) is a setting on
the collection. Every collection is in one of three states:

| State | Who can edit | Planning | View-only link |
|---|---|---|---|
| **Personal** | Just you (and your other devices) | Off by default; you may switch it on (a solo itinerary) and off again | No; you can start showing it |
| **Shown** | Just you | Off | Yes |
| **Planned** | You and the friends you shared it with | On, and it **stays on** | Never |

Rules that make the states hold:

- **More than one person editing means planning is on.** The only way to hand
  out the editable link is **Plan it together**, which switches planning on.
- **Planning can only be switched off while the collection is solo**: no
  browser other than the owner's devices has ever written to it (added,
  removed, voted, commented, …). Once someone else has joined, the switch
  goes away and planning stays on. Switching it off while solo **hides** any
  votes and plan; switching back on shows them again (nothing is deleted).
- **Only solo collections with planning off can be shown.** A group that
  wants to show what it planned duplicates the collection and shows the copy.
- **A shown collection only accepts writes from the owner's devices.** The
  view-only link never reveals the collection's own id, and if the editable
  address leaks anyway, other browsers' writes are refused ("Only Mira can
  change this collection").
- **New collections start with planning off. Existing collections** (made
  before this change) **keep planning on**; their owners can switch it off
  if they are still solo.

## Share

The Share sheet depends on the state (mock: board "1 · Share").

- **Personal:** "How should people use it?"
  - **Show it** — anyone with the link sees your picks and your comments;
    only you can change it. Asks once for **Show as** (prefilled with your
    name; a handle like `@sfjazzmira` is fine; editable later). Owner's
    devices only.
  - **Plan it together** — friends can add events, mark what they're
    interested in, comment, and build the plan with you. Switches planning
    on, then opens the system share sheet with the editable link.
  - *Send them a copy instead* (today's "Share a copy").
- **Shown:** **Copy link** (the view-only link), **Change "Show as"**, and
  **Stop showing**. To plan around it: **Plan with friends** (below), which
  leaves the shown collection untouched.
- **Planned:** **Copy link** (the editable link, for more friends), and
  *Send a copy instead*. No "Show it".

A shown collection's page says so in a strip under its note: "Shown with a
view-only link · Copy link" (mock: "Hybrid · Mira's collection").

**Stop showing** turns the view-only link off. Old links then say "This
collection isn't shown anymore". Showing again makes a **new** link.

## The view-only page

`canvas.html?v=<viewId>` (mock: "Hybrid · A follower opens Mira's link"):

- The collection's name and note, **"By <Show as>"**, "updated <time ago>".
- Its events (live items, cards as on the collection page, links to the
  event pages) with **only the owner's comments** (comments written from the
  owner's devices), shown under the "Show as" name.
- No votes, no plan, no activity, no remove, no comment box.
  "Only <Show as> can change this collection."
- It stays live: changes the owner makes appear on the next visit.
- Two buttons:
  - **Plan with friends** — makes *your own* copy with planning on (mock:
    "Hybrid · Tap Plan with friends"): asks for a name, says "Mira's
    collection stays as it is", then opens the new plan.
  - **Save a copy** — the same with planning off: your own personal list.
- Opening a view-only link adds it to the visitor's **Your collections**
  under a new **Following** group (beside Yours and Shared with you), which
  can be hidden like the others.

**Copies made from a view-only page** (mock: "Hybrid · The follower's new
plan") carry the live events and the owner's comments, labelled
"<Show as>, on their collection". The top line reads **"From <Show as>'s
collection"**, linking to the view-only page. The copy doesn't change when
the original does.

## Your other devices

"This is mine" is replaced, because anyone with the editable link could use
it to become an owner, and owners now have powers (showing, owner-only
comments on view pages, writes to shown collections).

- In the owner row at the bottom of a collection (where "Make this my
  default" is), owners see **Use on another device**. It makes a private
  one-time link (valid ~15 minutes) with Copy and Share buttons. Opening it
  on another device makes that browser an owner device and opens the
  collection. A QR code can come later.
- "This is mine" is no longer offered. Existing owner devices keep their
  status; "Not mine" (stop counting this browser) stays.
- Recovery when every owner device is lost: `canvas/scripts/admin.py` gains
  `add-owner <canvasId>`, which prints a device link, for the operator to
  send on request.

## The plan: tap a step to find it

Tapping a step in The plan scrolls the page to that event's card in the
collection and highlights the card briefly (~1.5 s). The step's title stops
being a link to the event page (the card has that link). A press and hold
still starts a drag. Keyboard: Enter on a focused step does the same. The
browser's Back doesn't need to undo the scroll.

## Data model (DynamoDB)

On `META`:
- `planning` (bool). **Absent = on** (collections made before this change);
  new collections are created with `false`.
- `view_id` — the current view-only link's id, absent when not shown.
- `show_as` — the curator name for view-only pages.
- `copied_from` — on copies made from a view-only page:
  `{view_id, name, show_as}`.

New rows:
- `PK = V#<viewId>`, `SK = VIEW` → `{canvas_id, created_at, stopped_at?}`.
  A stopped view keeps its row, so old links can say "isn't shown anymore".
  `viewId` is a fresh unguessable id (like collection ids), never derived
  from the collection's id.
- `PK = D#<token>`, `SK = DEVICE` → `{canvas_id, made_by_client, ttl}`. One
  use (deleted when used), expires via `ttl`.

On copied comments: `from_curator: true` and `name = show_as`.

"Solo" is computed, not stored: no item, vote or comment row (removed ones
included) written by a client outside the owner set.

## API

| Method and path | Who | What |
|---|---|---|
| `PATCH /canvases/{id}` with `planning` | anyone who can edit | `true`: always allowed. `false`: `409` unless solo. |
| `POST /canvases/{id}/show` `{show_as}` | owner devices | starts showing (`409` unless solo with planning off); returns `{view_id}`; already shown: returns the current one |
| `PATCH /canvases/{id}/show` `{show_as}` | owner devices | changes the curator name |
| `DELETE /canvases/{id}/show` | owner devices | stops showing |
| `GET /views/{viewId}` (`?if_version=`) | anyone | the view-only page's data (above); `410` when stopped; never returns the collection id |
| `POST /views/{viewId}/copy` `{name, planning}` | anyone (create rate limit) | `201` the new collection's full view |
| `POST /canvases/{id}/device-link` | owner devices | `{token, expires_at}` |
| `POST /device-links/{token}` | anyone holding it | makes this browser an owner device; returns `{canvas_id}`; `404` if used or expired |
| `POST /canvases/{id}/claim` | — | removed ("This is mine"); `DELETE` ("Not mine") stays |

- Vote and plan endpoints answer `409 planning is off` when planning is off.
- On a shown collection, every write from a non-owner client answers `403`.
- The full view (`GET /canvases/{id}`) adds `planning`, `solo`, `shown`
  (`{view_id, show_as}` for owner devices only) and `copied_from`.
- Activity gains `planning_on`, `planning_off`, `shown`, `stopped_showing`,
  `copied_from_view` (on the copy). Viewers never see activity.

## Frontend

- `canvas.html`: comments everywhere; votes and The plan follow `planning`
  (not today's "is it shared" rule, `isSocial`); the Planning switch in the
  header while solo; the per-state Share sheet; the "Shown" strip; the
  view-only page (`?v=`); Plan with friends / Save a copy; "From …" line;
  Following group; Use on another device; tap-a-step scrolling.
- `canvas-client.js`: view URLs, device links, the Following list in
  browser storage.
- `index.html`: collecting mode is unchanged (it only adds to collections you
  can edit).

## Tests

- API (moto): the planning rules (off only while solo; switch-off hides and
  keeps; new vs legacy default); showing (owner-only, solo-only, new id after
  stop, `410` after stop, no collection id leaked, owner-only comments,
  non-owner writes refused); copies (planning on/off, owner comments carried
  and labelled, `copied_from`); device links (one use, expiry, owner-only to
  make); `409` on votes/plan with planning off.
- End to end (`canvas/scripts/e2e_frontend.py`): a curator shows a list, a
  second browser opens the view link (read-only, Following), copies it with
  Plan with friends, invites a third browser; the curator's switch locks
  once someone else joins; Use on another device; tapping a plan step
  scrolls to its card.

## Rollout

One branch and PR (site + API). After it merges and deploys, launch
collections by setting `BETA_GATE = false` (`canvas/README.md` → "Private
beta"). Update `canvas/HOW-IT-WORKS.md`, the Collections entry in
`future/future-features.md`, and this spec's status.

## Later

- Readable addresses for shown collections (`/c/sf-jazz-picks`).
- View counts for curators.
- A **Report** link on view-only pages.
- A QR code for Use on another device.
