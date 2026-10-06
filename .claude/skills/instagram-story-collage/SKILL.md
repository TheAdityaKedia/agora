---
name: instagram-story-collage
description: Use when asked for an Instagram story, social image, screenshot collage or "show off" image for an Agora feature — the owner shares these with friends, not as marketing.
---

# Instagram story collage

A story frame is a **photograph of the real product**, not an advert for it: two or
three real screenshots, cropped tight, tilted, floating on the site's own paper
background. The UI's own words are the only words.

## Non-negotiables

- **Add no copy.** No headline, tagline, eyebrow, caption, label, call to
  action, URL, email address, logo, arrow or statistic. Text appears only
  because it is inside a screenshot. The owner rejected an earlier text frame as
  "very internet marketing — I am really only sharing with my friends."
- **Invent no visual identity.** No gradients, glows, dark mode, hairlines or
  decorative shapes. Canvas is flat `#eef1f2`; switch to `#e3e8ea` when the
  pieces are themselves paper-coloured (filter panels) and would otherwise
  blend. A piece whose own background is `#fff` (event cards) keeps `#eef1f2`.
  Site palette if you ever need it: ink `#13212e`, brick `#c0362c`.
- **Screenshot reality.** Drive the live site
  (`https://theadityakedia.github.io/agora/`) with real data. Never mock UI in
  HTML. If you restyle anything to read clearly — e.g. forcing a dropdown to
  show as selected — say so explicitly in your reply to the owner.
- **Check the beta gate first.** `MAP_BETA_GATE` in `frontend/index.html` hides
  the map (`view=map`) from normal visitors, and nothing else — neighbourhoods,
  search and filters are all public. Don't build a frame around something
  friends can't use without telling the owner.
- **Keep everything in the safe zone:** every piece between **y=250 and
  y=1580**. Instagram covers the top ~250 px and bottom ~340 px.

## The form

```html
<!doctype html><html><head><style>
html,body{margin:0}
body{width:1080px;height:1920px;background:#eef1f2;position:relative;overflow:hidden}
.p{position:absolute;overflow:hidden;box-shadow:0 30px 64px rgba(19,33,46,0.22)}
.p img{display:block;width:100%}
.a{left:60px;top:330px;width:790px;border-radius:30px;transform:rotate(-3deg)}
.b{z-index:2;left:430px;top:600px;width:540px;border-radius:30px;transform:rotate(3deg)}
</style></head><body>
<div class="p a"><img src="piece-a.png"></div>
<div class="p b"><img src="piece-b.png"></div>
</body></html>
```

Two or three pieces, tilts alternating within ±5°, overlapping slightly,
`z-index` deciding what sits on top. Put the HTML in the same directory as the
piece PNGs and load it over `file://` — fine for local images. (Only if you ever
load a webfont do you need `python3 -m http.server`; `file://` fails that CORS
check.)

## Workflow

0. **Find data that proves the point before you compose anything** — usually the
   bulk of the work. Probe candidates headlessly and read the results: for a
   search frame you want a query whose top hit matches on a *topic chip or venue
   line rather than the title*, with a description that isn't ugly (all-caps
   ticket notices, emoji walls). The list is capped around 60 cards and card
   order shifts between runs, so re-probe before selecting a result by index.
1. Capture the pieces from the live site with Playwright from `service/.venv`.
2. Compose the HTML in a scratch dir (`/tmp/story`, outside the repo).
3. Render at viewport 1080×1920, `device_scale_factor=1`.
4. **Measure, don't eyeball:** print each piece's top and bottom and check the
   safe zone.
5. **Look at the render** with the Read tool. Iterate on what you see — clipped
   text, a piece covering another's key line, slivers of neighbouring UI.
6. Save the final to `~/Downloads/agora-story-<topic>.png` and copy the HTML plus
   the piece PNGs and capture script to `~/Downloads/agora-story-source/`. That
   directory is shared by every story, so give the pieces a short per-story
   prefix (`nb-`, `lk-`, `se-`…) and number the HTML after the last one there.

Invoke python by absolute path — `/path/to/Agora/service/.venv/bin/python` — for
both Playwright and PIL. A `cd` does not persist between tool calls.

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    b = pw.chromium.launch(); p = b.new_page(viewport={"width": 1080, "height": 1920})
    p.goto("file:///tmp/story/collage.html"); p.wait_for_timeout(700)
    for s in [".a", ".b"]:
        bb = p.locator(s).bounding_box()
        print(s, round(bb["y"]), round(bb["y"] + bb["height"]))   # want 250..1580
    p.screenshot(path="/tmp/story/out.png"); b.close()
```

## Capturing clean pieces

Phone-shaped pieces: `viewport={"width":430,"height":1200}`,
`device_scale_factor=3`. **Omit `is_mobile`/`has_touch`** — with them the page
takes the `navigator.share` path and you lose the "Link copied" state.

Isolate the piece so the crop has no neighbouring slivers — move the elements
into a fixed box, **hide everything else**, and screenshot the box.
`locator.screenshot()` spills a few device pixels, so a card left rendered
behind the box leaks its chips into the bottom edge:

```python
ISO = """(sels)=>{const box=document.createElement('div');box.id='iso';
box.style.cssText='position:fixed;left:0;top:0;z-index:99999;background:#eef1f2;padding:26px 30px;display:inline-block';
document.body.appendChild(box);
for (const s of sels) box.appendChild(document.querySelector(s));
for (const el of [...document.body.children]) if (el.id!=='iso') el.style.display='none';}"""
p.evaluate(ISO, [".search-line", "article.event"])
p.locator(".search-line input").focus()   # reparenting BLURS it — refocus or lose the focus ring
p.mouse.move(429, 1199)                   # park the cursor: no hover states
p.locator("#iso").screenshot(path="piece.png")
```

Event cards: set the card's background to `#fff` and hide its `.cal` ("Add to
calendar"). That leaves `.tags` as the end of `.row-foot`, so the card already
ends on its chips — what needs neutralising is `.event`'s own
`border-bottom`/`padding-bottom`, which otherwise reads as a stray hairline at
the crop edge. Hide whole sections you don't need *before* capturing rather than
cropping around them. For pixel crops use PIL — `sips --cropOffset` does not do
what it looks like it does.

## Common mistakes

| Mistake | Instead |
|---|---|
| Writing a headline or tagline | No copy at all — the UI speaks |
| Dark mode, gradients, glows | Flat paper background, light theme |
| Safe zones from memory | y=250..1580, verified by measuring |
| Positioning by guess, then collisions | `bounding_box()` on every piece |
| Declaring done without looking | Read the PNG; review it |
| One big phone frame bleeding off-canvas | 2–3 tight pieces, fully inside |
| Hand-built HTML mock of the UI | Screenshot the real site |
| Picking the demo query/event last | Probe for proof data first — it's most of the work |

## Shipped examples

`~/Downloads/agora-story-source/` holds the four that shipped: `collage2.html`
(an Instagram post beside the event it became), `collage3.html` (a flyer photo
above the two events extracted from it), `collage5.html` (Area chips + the
neighbourhood dropdown), `collage6.html` (filters set → "Link copied" → saved
bookmarks). `agora-story-blank-white.png` and `-paper.png` are blank 1080×1920
intro frames.
