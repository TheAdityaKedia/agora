# Collections: user stories to test by hand

Twenty stories that walk through collections the way people use them:
1–10 cover sharing (`feature-specs/collection-sharing.md`), 11–20 the
basics. Test them in order: later stories reuse what earlier ones made.
How it all works: [`HOW-IT-WORKS.md`](HOW-IT-WORKS.md). All 20 passed in
headless Chromium on 2026-10-13; `scripts/e2e_frontend.py` automates most
of them.

## Setup

- Serve the site locally: `python3 -m http.server -d frontend 8000`, then
  open `http://localhost:8000/?beta=1`. Pages on `localhost` talk to the
  **dev** API (deployed from any branch). Or run the API locally too, with
  `?api=http://localhost:8787` (see [`README.md`](README.md) → "Frontend").
- You need three browsers, each with its own storage:
  - **A**: your main browser. You, the owner ("Mira" below; use any name).
  - **B**: a private window or a different browser. A friend or follower.
  - **C**: a second device or another browser profile. Your "other device".
    (For C on a phone, `localhost` won't reach it; a third browser profile is fine.)
- If a page says "Collections are in private beta", add `?beta=1` to the address.

---

## 1. Keep a personal list with notes to myself

**As a** person planning my own week, **I want** to save events into a list
and jot a note on each, **so that** I remember why I picked them.

1. In A, on the main site, tap **Start a collection**, name it "My October".
   Add three events from the list with the add buttons in the tray.
2. Open the collection (the tray's link, or **Your collections**).
3. On one event, type in **Add a comment** ("sit on the left") and send.

**Expect:** the collection lists the three events. The comment is saved
without asking for your name. There is **no 👍 Interested, no The plan, no
"Added by"** on the cards. The header has a **Planning** switch, set to off.
**Your collections** lists it under **Yours**.

## 2. Turn my list into a solo itinerary, and back

**As a** solo planner, **I want** to switch planning on just for me, **so
that** I can order my night without involving anyone.

1. In "My October" (A), switch **Planning** on.
2. Mark one event 👍 Interested. **Add to plan** on all three; drag a step
   (press and hold) to reorder it.
3. Tap a step's title in The plan (or focus it and press Enter).
4. Switch **Planning** off, then on again.

**Expect:** step 2 shows Interested and The plan. Tapping a step scrolls to
that event's card and flashes it briefly; it does not open the event page.
Switching off hides your vote and the plan; switching back on brings them
back unchanged (nothing deleted). Activity shows planning on/off entries.

## 3. Use my collection on another device

**As an** owner, **I want** to open my list on my phone as the owner, **so
that** I can edit and share it from either device.

1. In A, at the bottom of "My October", tap **Use on another device**. Copy
   the link.
2. Open it in C.
3. Open the same link again in B.

**Expect:** C lands on "My October" and sees the **Use on another device**
button and **Not mine** too (it is now an owner device). The **Planning**
switch is still there (your devices count as one person, so it's still
solo). In B the reused link is refused (used or expired); B does not
become an owner. A link left unused for 15 minutes also stops working.

## 4. Show my list to followers

**As a** curator, **I want** a view-only link to my picks, **so that**
followers can see them without being able to change anything.

1. In A, leave **Planning** on in "My October" (from story 2).
2. Tap **Share**. Pick **Show it**. **Show as** is prefilled with your name;
   change it to a handle like `@mirapicks`.
3. Copy the link it gives you.

**Expect:** Share offers three choices: **Show it**, **Plan it together**,
**Send a copy instead**. After showing, the page has a strip "Shown with a
view-only link, as @mirapicks", and the link is `canvas.html?v=…`. That `v`
id is **not** the collection's `c=` id. Share now offers the view-only link,
**Plan with friends**, **Change "Show as"** and **Stop showing**.
Showing switched **Planning** off by itself (a shown list has no planning),
and the switch is hidden while it's shown.

## 5. Follow a curator's list

**As a** follower, **I want** to open a curator's link and see their picks
and notes, **so that** I know what's worth going to.

1. Open the view-only link from story 4 in B.
2. Go to **Your collections** in B.

**Expect:** the page shows the name, note, "By @mirapicks", "updated …",
the events, and your comment from story 1 signed @mirapicks. No votes, no
plan, no Activity, no remove buttons, no comment box. It says "Only
@mirapicks can change this collection." Buttons: **Plan with friends** and
**Save a copy**. In B, **Your collections** has a **Following** group with
this list; it can be hidden.

## 6. Nobody else can change a shown list

**As a** curator, **I want** my shown list protected even if the editable
link leaks, **so that** only I decide what's on it.

1. In A, add a new event to "My October" and a comment on it.
2. Reload the view-only page in B.
3. Paste A's editable address (`canvas.html?c=…`) into B and look for a
   way to change it.
4. In C (your other device), add a comment.

**Expect:** B's view-only page now shows the new event and comment (it stays
live). In B the editable address opens read-only: no Share, Add, Remove,
renaming or comment box, and "Only @mirapicks can change this collection." C's comment goes
through, and shows on the view-only page signed @mirapicks.

## 7. Rename myself, stop showing, show again

**As a** curator, **I want** to change how I'm signed and to take a list
down, **so that** I control what's public.

1. In A, Share > **Change "Show as"** to "Mira". Reload B's view page.
2. Share > **Stop showing**, confirm. Reload B's view page.
3. Share > **Show it** again. Open the new link in B.

**Expect:** B shows "By Mira" after step 1. After stopping, the old link says
"This collection isn't shown anymore." Showing again gives a **different**
`v=` link; the old one still says it isn't shown, the new one works.

## 8. Plan with friends from someone's list

**As a** follower, **I want** to make my own copy of a curator's list,
**so that** I can plan with my friends without touching theirs.

1. In B, on the live view-only page, tap **Plan with friends**, give it a
   name, confirm.
2. Go back to the view page and tap **Save a copy**.
3. In A, look at "My October".

**Expect:** step 1 says the curator's collection stays as it is and opens
B's new collection: planning on, the curator's events, and their comments
labelled "Mira, on their collection". The top reads "From Mira's
collection", linking to the view page. Step 2 makes another copy with
planning **off** (a personal list). In A, "My October" is unchanged; no
items, votes or Activity from B.

## 9. Plan a night out together

**As a** friend organizing a night out, **I want** to send an editable link,
**so that** friends can add ideas, mark interest and build the plan with me.

1. In A, make a new collection "Saturday", add two events.
2. Share > **Plan it together**. Copy the link.
3. Back in A, before anyone opens it, look at the header and tap Share again.
4. Open the link in B, give a name, add an event, mark one 👍 Interested,
   comment, and **Add to plan**.

**Expect:** after step 2 planning is on and the **Planning** switch is gone
for good, even before B opens it; the header reads "Planning together". Share now just sends the editable link (no
Show it, no choices). In B everything works, and A sees B's name on votes,
comments, "Added by" and Activity after a refresh. **Your collections** in B
lists it under **Shared with you**.

## 10. Send a copy, and older collections

**As a** user, **I want** to hand someone their own copy, and my older
collections to keep working as before, **so that** nothing I made earlier
changes under me.

1. In A, make a new personal collection, Share > **Send a copy instead**.
   Open the copy's link in B, add an event.
2. Open a collection made **before** PR #128 (any older one in Your
   collections in A).

**Expect:** B's copy is its own collection; A's original doesn't change.
The older collection opens with planning **on** (votes and plan visible as
before). If only your devices ever wrote to it, the **Planning** switch is
there and turning it off hides votes and plan; if a friend ever touched it,
there's no switch.

---

## Round 2: the basics (11–20)

Same setup as above.

## 11. Get into the beta, and out of it

**As a** beta tester, **I want** collections to show only when beta is on,
**so that** the public site stays unchanged until launch.

1. In a fresh browser, open the plain site and `canvas.html`.
2. Open the invite link (`/beta/`).
3. Switch the **Beta** switch in the header off, then on.
4. Open the plain site in a new tab.

**Expect:** step 1 shows no "Start a collection", and `canvas.html` says
"Collections are in private beta". After the invite, collections show and
the header has a Beta switch. Off hides collections and on brings them back.
A new tab starts with beta off, and the switch is there to turn it on.

## 12. Collect events from the main site

**As a** person browsing listings, **I want** a tray that adds events to my
collection in one tap, **so that** I don't jump between pages.

1. Tap **Start a collection** and name it "Gallery hop". Add two events, then
   tap one of them again.
2. Reload the main page.
3. Tap **Done** in the tray.
4. In the collection, tap **Add events**.

**Expect:** the tray reads "Adding to Gallery hop · N items" and the count
follows your taps (tapping again removes the event). The tray survives a
reload. Done hides the tray and the add buttons. **Add events** brings the
tray back for this collection.

## 13. Add my own plans (dinner, a walk)

**As a** planner, **I want** to add things that aren't Agora events, **so
that** the whole evening is in one place.

1. In "Gallery hop", tap **Add your own**: "Dinner at Nopa", link
   `nopasf.com`, kind Food.
2. Edit it: change the kind to Drinks, clear the link, add a note.

**Expect:** a dashed card with the 🍽️ icon, "Any time", the title linking to
`https://nopasf.com`. After editing: 🍸 icon, no link, the note shows. Agora
events have no Edit button.

## 14. Name, note and dates

**As a** collection owner, **I want** to rename it and give it a note and
dates, **so that** friends know what it's for.

1. Tap the collection's name and rename it "Gallery hop, Fri".
2. Tap **Add a note**, write "start in Dogpatch".
3. Tap **Set dates** and pick a Friday to Saturday range.
4. Open **Your collections**.

**Expect:** the header shows the new name, the note and the date range.
Activity lists the rename, note and dates. Your collections shows the new
name with its dates.

## 15. Remove, undo and restore

**As a** collaborator, **I want** removals to be undoable, **so that** a
mis-tap never loses anything.

1. Remove an event, then tap **Undo** in the toast.
2. Remove it again and open **Activity**. Tap **Restore** on that line.
3. Switch Planning on, add an event to the plan, then remove that event.

**Expect:** Undo brings it back right away. Activity reads "Activity · 1
removed", the line says "You removed …" with its date, and Restore brings it
back (not into the plan). In step 3 you're asked first ("It's in the plan").

## 16. Take the plan to my calendar

**As a** planner, **I want** the plan in my calendar, **so that** I get
reminders.

1. With two dated steps in The plan, tap **Add the plan to your calendar**.
2. Look at a step's **Google Calendar** link.

**Expect:** a `.ics` file downloads, named after the collection, with one
event per dated step. The Google Calendar link opens a prefilled event with
that step's title and time.

## 17. Make a collection my default

**As a** frequent saver, **I want** one collection as my default, **so that**
quick saves land there.

1. In a collection, tap **Make this my default**.
2. Open **Your collections**.
3. Back in the collection, tap **Unset**.

**Expect:** the header says "★ Your default". In Your collections it is
pinned first under Yours, marked "★ Default". Unset removes both.

## 18. Duplicate for myself

**As a** person reusing a good list, **I want** a copy of a collection,
**so that** I can change it without touching the original.

1. In a planned collection with votes, tap **Duplicate this collection** and
   name it.
2. Add your own item to the copy, then go back to the original.

**Expect:** the copy has the same live items, planning **off** (no votes, no
plan), and its Activity says it was made as a copy. The original is
unchanged.

## 19. Tidy my list of collections

**As a** person with many collections, **I want** to hide old ones and fix
which device counts as mine, **so that** my list stays clean.

1. In Your collections, tap **Hide** on one.
2. Open the hidden one's link again.
3. On a browser that became yours through **Use on another device**, tap
   **Not mine**.

**Expect:** Hide removes it from the list only. Opening it again brings it
back. Not mine moves it to **Shared with you** on that browser, and that
browser is no longer an owner (no Use on another device).

## 20. See friends' changes without reloading

**As a** collaborator, **I want** my open page to pick up others' changes,
**so that** we don't talk past each other.

1. A and B both have a planned collection open.
2. B adds an item and comments. A waits (or switches tabs and back).
3. While A is typing a comment, B makes another change.

**Expect:** A's page updates on its own (every 30 seconds, and right away on
returning to the tab). It doesn't redraw while A is typing. A's half-written
comment stays, and the update lands once A leaves the field.
