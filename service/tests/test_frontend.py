"""Headless-browser checks for frontend/index.html.

Serves a copy of frontend/ with a generated events.json and drives it in
Chromium at phone and desktop sizes. Skipped when no Chromium is available
(`playwright install chromium`, or point AGORA_CHROMIUM at a binary).
"""
import functools
import http.server
import json
import os
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}
TZ = "America/Los_Angeles"
N_EVENTS = 400
# One busy day (day 5) gets BUSY extra events, so it has 4 + BUSY.
BUSY = 15
DAY_CAP = 10
TOTAL = N_EVENTS + BUSY

TAXONOMY = {
    "version": 1,
    "axes": {
        "type": {"tree": {
            "performance": {"label": "Performance / Show",
                            "children": {"concert": {"label": "Concert"}}},
            "talk": {"label": "Talk"},
        }},
        "topic": {"values": {
            "jazz": {"label": "Jazz", "group": "Music"},
            "poetry": {"label": "Poetry", "group": "Literary"},
        }},
    },
}


def _events(n=N_EVENTS):
    """`n` events, four a day from tomorrow on, alternating jazz / poetry."""
    # "Tomorrow" in the page's timezone, not UTC: they differ for 7h a day.
    base = (datetime.now(ZoneInfo(TZ)) + timedelta(days=1)).replace(
        hour=12, minute=0, second=0, microsecond=0)
    out = []
    for i in range(n):
        jazz = i % 2 == 0
        out.append({
            "id": f"ev{i}",
            "title": f"Jazz night {i}" if jazz else f"Poetry reading {i}",
            "start_time": (base + timedelta(days=i // 4, hours=i % 4)).isoformat(),
            # Jazz: one venue written two ways (the second with a room).
            "location": ("20 Annie St — Back Bar" if i % 4 == 2 else "The Dawn Club, San Francisco")
                        if jazz else "261 Columbus Ave",
            "url": f"https://example.com/e/{i}",
            # Only "Doors at seven." shows until "more"; the rest (and the
            # word "zanzibar" in event 5) is in descriptions.json.
            "description": "Doors at seven. Bring a friend and stay late."
                           + (" The Zanzibar quartet plays." if i == 5 else ""),
            "image_url": None,
            "sources": ["The Dawn Club"] if jazz else ["City Lights"],
            "types": [["performance", "concert"]] if jazz else [["talk"]],
            "topics": ["jazz"] if jazz else ["poetry"],
            "cost": "unknown",
            # Every 10th event has no known area.
            "region": None if i % 10 == 9 else ("sf" if jazz else "eastbay"),
            "venue": "dawn-club" if jazz else None,
            **({"room": "Back Bar"} if jazz and i % 4 == 2 else {}),
        })
    busy_start = base + timedelta(days=5, hours=4)
    for i in range(BUSY):
        out.append({**out[0], "id": f"busy{i}", "title": f"Busy day show {i}",
                    "url": f"https://example.com/busy/{i}",
                    "start_time": (busy_start + timedelta(minutes=10 * i)).isoformat()})
    # Started an hour ago: still in the manifest (it's pruned by calendar day)
    # but must never show.
    out.append({**out[0], "id": "started", "title": "Already started",
                "url": "https://example.com/started",
                "start_time": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()})
    out.sort(key=lambda e: e["start_time"])
    # Event index 2 has an image that fails to load (nothing listens on port 9).
    for e in out:
        if e["id"] == "ev2":
            e["image_url"] = "http://127.0.0.1:9/missing.png"
    # Unbreakable strings that used to widen the page past a phone screen.
    out[0]["title"] = "BATIASHVILI/CAPUÇON/THIBAUDET/TRIO/" * 3
    out[1]["description"] = (
        "Apply: https://airtable.com/" + "x" * 120 + " then come to the show. More.")
    return out


def _manifest(events):
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "taxonomy": TAXONOMY,
        "regions": [{"id": "sf", "label": "San Francisco"}, {"id": "eastbay", "label": "East Bay"},
                    {"id": "northbay", "label": "North Bay"}],
        "venues": {"dawn-club": {"name": "The Dawn Club", "region": "sf",
                                 "address": "20 Annie St, San Francisco, CA 94105"}},
        "events": events,
    }


def _serve(root):
    shutil.copy(FRONTEND / "index.html", root / "index.html")
    shutil.copytree(FRONTEND / "vendor", root / "vendor")
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """The manifest as the exporter writes it: summaries in events.json, full
    descriptions in descriptions.json (feature-specs/frontend-payload.md)."""
    from exporters.json_export import split_descriptions
    root = tmp_path_factory.mktemp("site")
    events = _events()
    (root / "descriptions.json").write_text(json.dumps(split_descriptions(events)))
    (root / "events.json").write_text(json.dumps(_manifest(events)))
    server = _serve(root)
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture(scope="module")
def legacy_site(tmp_path_factory):
    """An older manifest with descriptions inline and no descriptions.json."""
    root = tmp_path_factory.mktemp("legacy")
    (root / "events.json").write_text(json.dumps(_manifest(_events())))
    server = _serve(root)
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        # Playwright's own download first, then an explicit binary.
        exes = [None, os.environ.get("AGORA_CHROMIUM") or "/opt/pw-browsers/chromium"]
        b = last_err = None
        for exe in exes:
            try:
                b = p.chromium.launch(executable_path=exe)
                break
            except Exception as e:  # browser not installed at this path
                last_err = e
        if b is None:
            pytest.skip(f"no Chromium available: {last_err}")
        yield b
        b.close()


def _open(browser, site, viewport, query="", touch=False):
    ctx = browser.new_context(viewport=viewport, is_mobile=touch, has_touch=touch,
                              timezone_id=TZ)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(site + query)
    page.wait_for_selector(".event", timeout=15000)
    page._agora_errors = errors
    return page


def _rendered(page):
    return page.locator(".event").count()


def test_loads_without_errors(browser, site):
    page = _open(browser, site, DESKTOP)
    assert page._agora_errors == []
    assert _rendered(page) > 0


def test_no_horizontal_overflow_on_phone(browser, site):
    page = _open(browser, site, PHONE, touch=True)
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= PHONE["width"], f"page is {width}px wide on a {PHONE['width']}px phone"


def test_renders_incrementally_and_loads_more_on_scroll(browser, site):
    page = _open(browser, site, DESKTOP)
    first = _rendered(page)
    # Every row except the busy day's overflow, which waits for "Show more".
    expected = TOTAL - (4 + BUSY - DAY_CAP)
    assert 0 < first < expected
    for _ in range(40):
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(50)
        if _rendered(page) == expected:
            break
    assert _rendered(page) == expected


def test_search_filters_and_ranks(browser, site):
    page = _open(browser, site, DESKTOP)
    page.fill("#search-input", "poetry")
    page.wait_for_function(
        "document.querySelector('.event .title') &&"
        " document.querySelector('.event .title').textContent.includes('Poetry')")
    titles = page.locator(".event .title").all_inner_texts()
    assert titles and all("Poetry" in t for t in titles)
    assert "q=poetry" in page.url


def test_shared_search_link_renders(browser, site):
    page = _open(browser, site, DESKTOP, query="?q=jazz")
    page.wait_for_function(
        "[...document.querySelectorAll('.event .title')].every(t => !t.textContent.includes('Poetry'))")
    assert page._agora_errors == []


def test_only_buttons_visible_on_touch(browser, site):
    page = _open(browser, site, PHONE, touch=True)
    page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
    only = page.locator("#source-list .only-btn").first
    assert only.evaluate("e => getComputedStyle(e).visibility") == "visible"


def test_filter_sheet_opens_and_closes_on_phone(browser, site):
    page = _open(browser, site, PHONE, touch=True)
    sheet = page.locator("#filters")
    assert not sheet.is_visible()
    page.click("#filters-btn")
    page.wait_for_selector("#filters.open")
    assert f"Show {TOTAL} events" in page.inner_text("#filters-done")
    page.click("#filters-done")
    page.wait_for_selector("#filters:not(.open)", state="attached")
    page.click("#filters-btn")
    page.keyboard.press("Escape")
    page.wait_for_selector("#filters:not(.open)", state="attached")


def test_active_filter_pill_shows_and_clears(browser, site):
    page = _open(browser, site, PHONE, touch=True)
    page.click("#preset-tomorrow")
    pill = page.locator("#active-filters .active-pill")
    assert pill.count() == 1 and "Tomorrow" in pill.inner_text()
    assert page.inner_text("#filters-count") == "1"
    assert _rendered(page) == 4
    page.click("#active-filters .pill-clear")
    assert page.locator("#active-filters .active-pill").count() == 0
    assert "dates=" not in page.url


def test_day_strip_jumps_past_rendered_rows(browser, site):
    page = _open(browser, site, PHONE, touch=True)
    days = page.locator("#day-strip .day-btn")
    assert days.count() > 20
    before = _rendered(page)
    target = days.nth(days.count() - 1)
    day = target.get_attribute("data-day")
    target.click()
    heading = page.locator(f'h2.date[data-day="{day}"]')
    assert heading.count() == 1
    assert _rendered(page) > before
    top = heading.evaluate("e => e.getBoundingClientRect().top")
    assert 0 <= top < 300


def test_source_always_shown(browser, site):
    page = _open(browser, site, DESKTOP)
    jazz, poetry = page.locator(".event").nth(2), page.locator(".event").nth(3)
    assert "Jazz night 2" in jazz.inner_text()
    # Shown even though the venue has the same name.
    assert jazz.locator(".source").inner_text() == "The Dawn Club"
    assert "Poetry reading 3" in poetry.inner_text()
    assert poetry.locator(".source").inner_text() == "City Lights"


def test_location_click_filters_with_pill(browser, site):
    page = _open(browser, site, DESKTOP)
    page.locator(".event").nth(3).locator(".location-btn").click()
    assert "261 Columbus Ave" in page.inner_text("#active-filters")
    assert "loc=" in page.url
    assert all("Poetry" in t for t in page.locator(".event .title").all_inner_texts())


def test_desktop_shows_filters_as_sidebar(browser, site):
    page = _open(browser, site, DESKTOP)
    assert page.locator("#filters").is_visible()
    assert not page.locator("#filters-btn").is_visible()
    sidebar = page.locator("#filters").bounding_box()
    first = page.locator(".event").first.bounding_box()
    assert sidebar["x"] + sidebar["width"] <= first["x"]
    # A tall sidebar must not ride up over the masthead (the phone sheet's
    # `bottom: 0` once pulled it up under the sticky rules).
    short = _open(browser, site, {"width": 1440, "height": 500})
    h1 = short.locator("h1").bounding_box()
    assert short.locator("#filters").bounding_box()["y"] >= h1["y"] + h1["height"]


def test_slash_focuses_search_and_meta_is_relative(browser, site):
    page = _open(browser, site, DESKTOP)
    page.keyboard.press("/")
    assert page.evaluate("document.activeElement.id") == "search-input"
    assert "Updated" in page.inner_text("#meta")


def _busy_day(page):
    return page.locator("section.day", has_text="Busy day show 0")


def test_day_capped_with_show_more(browser, site):
    page = _open(browser, site, DESKTOP)
    day = _busy_day(page)
    day.scroll_into_view_if_needed()
    assert day.locator(".event").count() == DAY_CAP
    more = day.locator(".day-more")
    assert more.inner_text().startswith(f"Show {4 + BUSY - DAY_CAP} more on ")
    more.click()
    assert day.locator(".event").count() == 4 + BUSY
    assert day.locator(".day-more").count() == 0
    # Other days are unaffected.
    assert page.locator("section.day").first.locator(".event").count() == 4


def test_single_day_results_are_not_capped(browser, site):
    page = _open(browser, site, DESKTOP)
    key = _busy_day(page).get_attribute("data-day")
    page = _open(browser, site, DESKTOP, query=f"?from={key}&to={key}")
    assert _rendered(page) == 4 + BUSY
    assert page.locator(".day-more").count() == 0


def test_started_events_never_show(browser, site):
    page = _open(browser, site, DESKTOP)
    assert page.locator(".event", has_text="Already started").count() == 0
    assert f"{TOTAL:,} upcoming events" in page.inner_text("#meta")
    page.fill("#search-input", "already started")
    page.wait_for_timeout(600)
    assert page.locator(".event", has_text="Already started").count() == 0


def test_source_click_filters_and_toggles_back(browser, site):
    page = _open(browser, site, DESKTOP)
    page.locator(".event").nth(3).locator(".source-btn").click()
    assert "City Lights" in page.inner_text("#active-filters")
    assert "sources=City+Lights" in page.url or "sources=City%20Lights" in page.url
    titles = page.locator(".event .title").all_inner_texts()
    assert titles and all("Poetry" in t for t in titles)
    page.locator(".event").first.locator(".source-btn").click()
    assert page.locator("#active-filters .active-pill").count() == 0
    assert "sources=" not in page.url


def test_weekend_preset(browser, site):
    page = _open(browser, site, DESKTOP)
    page.click("#preset-weekend")
    assert "dates=weekend" in page.url
    assert "active" in page.get_attribute("#preset-weekend", "class")
    # On a Sunday "this weekend" is just the rest of today, and the fixture's
    # events start tomorrow: nothing to show is then correct.
    if datetime.now(ZoneInfo(TZ)).weekday() != 6:
        assert _rendered(page) > 0
    # Friday from 5pm, Saturday, Sunday — and nothing else.
    assert page.evaluate("""[...document.querySelectorAll('.event')].every(e => {
        const d = new Date(e.dataset.start), w = d.getDay();
        return w === 6 || w === 0 || (w === 5 && d.getHours() >= 17);
    })""")
    page.click("#preset-weekend")
    assert "dates=" not in page.url
    # Survives a reload as the symbolic preset (waiting for the preset, not
    # for events: there may be none, see above).
    page.goto(site + "?dates=weekend")
    page.wait_for_selector("#preset-weekend.active", timeout=15000)
    assert "This weekend" in page.inner_text("#active-filters")


def test_add_to_calendar_downloads_ics(browser, site):
    page = _open(browser, site, DESKTOP)
    row = page.locator(".event").nth(3)  # "Poetry reading 3"
    row.locator(".cal-btn").click()
    with page.expect_download() as dl:
        row.locator(".cal-ics").click()
    download = dl.value
    assert download.suggested_filename == "Poetry-reading-3.ics"
    ics = open(download.path(), encoding="utf-8", newline="").read()
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert "SUMMARY:Poetry reading 3\r\n" in ics
    assert "LOCATION:261 Columbus Ave\r\n" in ics
    start = row.get_attribute("data-start")
    want = datetime.fromisoformat(start).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    assert f"DTSTART:{want}\r\n" in ics
    assert "URL:https://example.com/e/3\r\n" in ics
    # Every physical line is at most 75 bytes (RFC 5545 folding).
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))


def test_thumbnail_tiles_for_missing_and_broken_images(browser, site):
    page = _open(browser, site, DESKTOP)
    rows = page.locator(".event")
    assert page.locator(".event .thumb").count() == rows.count()
    jazz, poetry = rows.nth(2), rows.nth(3)
    # No image: a tile labelled with the event's type.
    assert poetry.locator(".thumb-label").inner_text() == "Talk"
    assert poetry.locator(".thumb img").count() == 0
    # Broken image: removed on error, leaving the tile.
    page.wait_for_function(
        "!document.querySelectorAll('.event')[2].querySelector('.thumb img')")
    assert jazz.locator(".thumb-label").inner_text() == "Show"  # of "Performance / Show"


def test_day_strip_tracks_scroll_and_stays_visible(browser, site):
    page = _open(browser, site, PHONE, touch=True)
    first = page.locator("#day-strip .day-btn").first
    page.wait_for_function("document.querySelector('#day-strip .day-btn.current')")
    assert "current" in first.get_attribute("class")
    # Scroll the 4th day's section to just under the sticky bar.
    key = page.locator("section.day").nth(3).get_attribute("data-day")
    page.locator(f'h2.date[data-day="{key}"]').evaluate("e => e.scrollIntoView()")
    page.wait_for_function(
        f"document.querySelector('#day-strip .day-btn.current')?.dataset.day === '{key}'")
    assert page.get_attribute("#day-strip .day-btn.current", "aria-current") == "date"
    # The strip is in the sticky bar, so it's still on screen.
    top = page.locator("#day-strip").bounding_box()["y"]
    assert 0 <= top < 200


def test_add_to_google_calendar_link(browser, site):
    from urllib.parse import parse_qs, urlparse
    page = _open(browser, site, DESKTOP)
    row = page.locator(".event").nth(3)  # "Poetry reading 3"
    row.locator(".cal-btn").click()
    link = row.locator("a.cal-google")
    assert link.is_visible()
    assert link.get_attribute("target") == "_blank"
    url = urlparse(link.get_attribute("href"))
    assert url.netloc == "calendar.google.com"
    q = parse_qs(url.query)
    assert q["action"] == ["TEMPLATE"]
    assert q["text"] == ["Poetry reading 3"]
    assert q["location"] == ["261 Columbus Ave"]
    start = datetime.fromisoformat(row.get_attribute("data-start")).astimezone(timezone.utc)
    fmt = "%Y%m%dT%H%M%SZ"
    assert q["dates"] == [f"{start:{fmt}}/{start + timedelta(hours=2):{fmt}}"]
    assert "https://example.com/e/3" in q["details"][0]
    # One menu at a time; a click elsewhere closes it.
    page.locator(".event").nth(2).locator(".cal-btn").click()
    assert not link.is_visible()
    page.click("h1")
    assert page.locator("details.cal[open]").count() == 0


def test_thumbnail_labels_fit_their_tiles(browser, site):
    page = _open(browser, site, PHONE, touch=True)
    assert page.evaluate("""[...document.querySelectorAll('.thumb-label')]
        .every(l => l.scrollWidth <= l.parentNode.clientWidth)""")


def test_area_filter(browser, site):
    page = _open(browser, site, DESKTOP)
    chips = page.locator("#area-chips [data-area]")
    # Only regions that have events get a chip.
    assert chips.all_inner_texts() == ["San Francisco", "East Bay"]
    page.click('#area-chips [data-area="eastbay"]')
    assert "area=eastbay" in page.url
    regions = page.locator(".event").evaluate_all("els => els.map(e => e.dataset.region)")
    assert regions and set(regions) == {"eastbay"}
    assert "no known area" in page.inner_text("#meta")
    assert "East Bay" in page.inner_text("#active-filters")
    # A second area widens (OR); the pill clears one.
    page.click('#area-chips [data-area="sf"]')
    regions = page.locator(".event").evaluate_all("els => els.map(e => e.dataset.region)")
    assert set(regions) == {"sf", "eastbay"}
    page.locator("#active-filters .pill-clear").first.click()
    page.locator("#active-filters .pill-clear").first.click()
    assert "area=" not in page.url and "no known area" not in page.inner_text("#meta")
    # Shared link.
    page = _open(browser, site, DESKTOP, query="?area=sf")
    assert set(page.locator(".event").evaluate_all("els => els.map(e => e.dataset.region)")) == {"sf"}
    assert page.get_attribute('#area-chips [data-area="sf"]', "aria-pressed") == "true"


def test_rows_show_the_venue_name_and_room(browser, site):
    page = _open(browser, site, DESKTOP)
    rows = page.locator(".event")
    plain, room, poetry = (rows.nth(i).locator(".location-btn") for i in (0, 2, 3))
    assert plain.inner_text() == "The Dawn Club"
    assert room.inner_text() == "The Dawn Club · Back Bar"
    tip = room.get_attribute("title")
    assert "20 Annie St — Back Bar" in tip and "20 Annie St, San Francisco, CA 94105" in tip
    assert poetry.inner_text() == "261 Columbus Ave"  # no venue: the source's text
    assert "OpenStreetMap" in page.inner_text(".credits")


def test_venue_click_shows_every_spelling_with_pill_and_link(browser, site):
    page = _open(browser, site, DESKTOP)
    page.locator(".event").nth(2).locator(".location-btn").click()
    assert "venue=dawn-club" in page.url and "loc=" not in page.url
    assert "The Dawn Club" in page.inner_text("#active-filters")
    titles = page.locator(".event .title").all_inner_texts()
    assert titles and all("Jazz" in t or "Busy" in t or "BATIASHVILI" in t for t in titles)
    texts = page.locator(".event .location-btn").all_inner_texts()
    assert "The Dawn Club" in texts and "The Dawn Club · Back Bar" in texts
    # A text filter replaces it (and vice versa).
    page.click("#reset")
    page.locator(".event").nth(3).locator(".location-btn").click()
    assert "loc=" in page.url and "venue=" not in page.url
    # The link survives a reload; an unknown id is ignored.
    page = _open(browser, site, DESKTOP, "?venue=dawn-club")
    assert all("Poetry" not in t for t in page.locator(".event .title").all_inner_texts())
    page = _open(browser, site, DESKTOP, "?venue=gone")
    assert page.inner_text("#active-filters").strip() == "" and page._agora_errors == []


def test_search_finds_events_by_venue_name(browser, site):
    page = _open(browser, site, DESKTOP)
    page.fill("#search-input", "Dawn Club Back Bar")
    page.wait_for_function("document.querySelector('.event .location-btn')?.textContent"
                           " === 'The Dawn Club · Back Bar'")
    assert set(page.locator(".event .location-btn").all_inner_texts()) == {"The Dawn Club · Back Bar"}


def test_calendar_uses_venue_name_and_address(browser, site):
    from urllib.parse import parse_qs, urlparse
    page = _open(browser, site, DESKTOP)
    row = page.locator(".event").nth(2)
    row.locator(".cal-btn").click()
    q = parse_qs(urlparse(row.locator("a.cal-google").get_attribute("href")).query)
    assert q["location"] == ["The Dawn Club · Back Bar, 20 Annie St, San Francisco, CA 94105"]


# --- descriptions on demand (feature-specs/frontend-payload.md) ----------------

def _desc_requests(page):
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if r.url.endswith("descriptions.json") else None)
    return reqs


def test_descriptions_load_only_on_more(browser, site):
    ctx = browser.new_context(viewport=DESKTOP, timezone_id=TZ)
    page = ctx.new_page()
    reqs = _desc_requests(page)
    page.goto(site)
    page.wait_for_selector(".event")
    page.wait_for_timeout(500)
    assert reqs == []  # not fetched for the first render
    desc = page.locator(".event").nth(3).locator(".description")
    assert desc.inner_text().startswith("Doors at seven.") and "Bring a friend" not in desc.inner_text()
    desc.click()
    page.wait_for_selector(".event:nth-child(4) .description.expanded, .description.expanded")
    assert "Bring a friend and stay late." in page.locator(".description.expanded").first.inner_text()
    assert len(reqs) == 1
    # Collapsing and expanding another needs no new request.
    page.locator(".description.expanded").first.click()
    page.locator(".event").nth(5).locator(".description").click()
    page.wait_for_selector(".description.expanded")
    assert len(reqs) == 1


def test_search_reaches_full_descriptions(browser, site):
    page = _open(browser, site, DESKTOP)
    page.fill("#search-input", "zanzibar")
    page.wait_for_function("document.querySelectorAll('.event').length === 1", timeout=15000)
    assert "Jazz night" in page.locator(".event .title").inner_text() or \
        "Poetry" in page.locator(".event .title").inner_text()
    assert page._agora_errors == []


def test_shared_search_link_reaches_full_descriptions(browser, site):
    page = _open(browser, site, DESKTOP, "?q=zanzibar")
    page.wait_for_function("document.querySelectorAll('.event').length === 1", timeout=15000)


def test_legacy_manifest_with_inline_descriptions(browser, legacy_site):
    ctx = browser.new_context(viewport=DESKTOP, timezone_id=TZ)
    page = ctx.new_page()
    reqs = _desc_requests(page)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(legacy_site)
    page.wait_for_selector(".event")
    page.locator(".event").nth(3).locator(".description").click()
    page.wait_for_selector(".description.expanded")
    page.fill("#search-input", "zanzibar")
    page.wait_for_function("document.querySelectorAll('.event').length === 1", timeout=15000)
    assert reqs == [] and errors == []


# --- map view + SF neighborhoods (feature-specs/venues.md, phase 4) -----------

DAWN = (-122.4013, 37.7876)    # 20 Annie St (lng, lat)
ROXIE = (-122.42257, 37.76493)
ALAMO = (-122.4347, 37.77636)


def _map_events():
    """The usual events, with some jazz nights at the Roxie (Mission), one at
    Alamo Square (a street-precision, approximate pin) — and the poetry
    readings, which have no venue, so no map location."""
    out = _events()
    for e in out:
        n = int(e["id"][2:]) if e["id"].startswith("ev") else -1
        if n % 8 == 4:
            e.update(venue="roxie", location="Roxie Theater", region="sf")
        elif n == 6:
            e.update(venue="alamo-square", location="Alamo Square", region="sf")
    return out


def _map_manifest(events):
    m = _manifest(events)
    m["venues"] = {
        "dawn-club": {**m["venues"]["dawn-club"], "lng": DAWN[0], "lat": DAWN[1], "neighborhood": "soma"},
        "roxie": {"name": "Roxie Theater", "region": "sf", "address": "3117 16th St, San Francisco, CA",
                  "lng": ROXIE[0], "lat": ROXIE[1], "neighborhood": "mission"},
        "alamo-square": {"name": "Alamo Square", "region": "sf", "address": "San Francisco, CA 94117",
                         "lng": ALAMO[0], "lat": ALAMO[1], "approx": True},
    }
    m["neighborhoods"] = [{"id": "haight-ashbury", "label": "Haight Ashbury"},
                          {"id": "mission", "label": "Mission"}, {"id": "soma", "label": "SoMa"}]
    return m


@pytest.fixture(scope="module")
def map_site(tmp_path_factory):
    from exporters.json_export import split_descriptions
    root = tmp_path_factory.mktemp("mapsite")
    events = _map_events()
    (root / "descriptions.json").write_text(json.dumps(split_descriptions(events)))
    (root / "events.json").write_text(json.dumps(_map_manifest(events)))
    # The beta switch lives in canvas-client.js (shared with collections).
    shutil.copy(FRONTEND / "canvas-client.js", root / "canvas-client.js")
    server = _serve(root)
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


def _map_page(browser, site, viewport=DESKTOP, query="", touch=False, beta=True):
    """A page with the map's tile server unreachable (as in CI: no network),
    so the map falls back to pins on a blank background. `beta`: the browser
    is a beta member (what frontend/beta/ sets) with beta switched on for this
    visit; "member" = a member with it switched off."""
    ctx = browser.new_context(viewport=viewport, is_mobile=touch, has_touch=touch, timezone_id=TZ)
    if beta:
        ctx.add_init_script("localStorage.setItem('agora.canvas.beta', 'true');"
                            + ("" if beta == "member" else "sessionStorage.setItem('agora.beta.on', '1');"))
    ctx.route("https://tiles.openfreemap.org/**", lambda route: route.abort())
    page = ctx.new_page()
    page._agora_errors = []
    page._agora_requests = []
    page.on("pageerror", lambda e: page._agora_errors.append(str(e)))
    page.on("request", lambda r: page._agora_requests.append(r.url))
    page.goto(site + query)
    page.wait_for_selector(".event, #map-wrap:not([hidden])", timeout=15000)
    return page


def _wait_for_pins(page):
    page.wait_for_function("performance.getEntriesByName('agora:map-pins').length > 0", timeout=20000)


def _pins(page):
    """{venue id: event count} as the map's source has it."""
    return page.evaluate("""(async () => {
        const data = await agoraMap.getSource('venues').getData();
        return Object.fromEntries(data.features.map(f => [f.properties.venue, f.properties.events]));
    })()""")


def _click_lnglat(page, lnglat):
    pt = page.evaluate("(ll) => { const p = agoraMap.project(ll); return [p.x, p.y]; }", list(lnglat))
    box = page.locator("#map").bounding_box()
    page.mouse.click(box["x"] + pt[0], box["y"] + pt[1])


def _settle(page):
    page.wait_for_function("agoraMap.loaded() && !agoraMap.isMoving()", timeout=10000)


def test_map_ui_only_with_coordinates(browser, site, legacy_site, map_site):
    # Today's manifest (venues without coordinates or neighborhoods), and an
    # older one: no toggle, no neighborhood filter, and ?view=map is ignored.
    for s in (site, legacy_site):
        page = _map_page(browser, s, query="?view=map&at=37.77,-122.42,13&hood=mission")
        assert page.locator("#view-toggle").is_hidden()
        assert page.locator("#hood-dropdown").is_hidden()
        assert page.locator("#map-wrap").is_hidden() and _rendered(page) > 0
        assert "view=" not in page.url and "hood=" not in page.url
        assert not any("maplibre" in u for u in page._agora_requests)
        assert page._agora_errors == []
    page = _map_page(browser, map_site)
    assert page.locator("#view-toggle").is_visible() and page.locator("#hood-dropdown").is_visible()


def test_map_library_loads_only_when_the_map_opens(browser, map_site):
    page = _map_page(browser, map_site, viewport=PHONE, touch=True)
    page.wait_for_timeout(800)  # past the first render and the index build
    assert not any("maplibre" in u for u in page._agora_requests)
    page.click('[data-view="map"]')
    _wait_for_pins(page)
    loaded = [u.rsplit("/", 1)[1] for u in page._agora_requests if "maplibre" in u]
    assert "maplibre-gl.mjs" in loaded and "maplibre-gl.css" in loaded
    assert "view=map" in page.url and page.locator("#list").is_hidden()
    assert page.get_attribute('[data-view="map"]', "aria-pressed") == "true"
    # Tiles unreachable: pins on a blank background, and the map says so.
    assert "Base map unavailable" in page.inner_text("#map-status")
    assert page._agora_errors == []
    # Back to the list.
    page.click('[data-view="list"]')
    assert page.locator("#map-wrap").is_hidden() and _rendered(page) > 0 and "view=" not in page.url


def test_pins_are_per_venue_and_follow_the_filters(browser, map_site):
    events = [e for e in _map_events() if e["id"] != "started"]
    want = {}
    for e in events:
        if e["venue"]:
            want[e["venue"]] = want.get(e["venue"], 0) + 1
    page = _map_page(browser, map_site, query="?view=map")
    _wait_for_pins(page)
    assert _pins(page) == want
    no_venue = sum(1 for e in events if not e["venue"])
    assert f"{no_venue} events have no map location" in page.inner_text("#meta")
    # Type: talks are the poetry readings, none of which has a venue.
    page.click('#type-chips [data-tagval="talk"]')
    page.wait_for_function("document.getElementById('map-wrap').dataset.venues === '0'")
    assert _pins(page) == {}
    assert "No events here have a map location" in page.inner_text("#map-status")
    page.click('#type-chips [data-tagval="talk"]')
    # Search narrows the pins too.
    page.fill("#search-input", "Busy day")
    page.wait_for_function("document.getElementById('map-wrap').dataset.venues === '1'")
    assert _pins(page) == {"dawn-club": BUSY}
    page.fill("#search-input", "")
    # The venue filter: one pin.
    page = _map_page(browser, map_site, query="?view=map&venue=roxie")
    _wait_for_pins(page)
    assert list(_pins(page)) == ["roxie"]


def test_clicking_a_pin_lists_its_events(browser, map_site):
    roxie = sum(1 for e in _map_events() if e["venue"] == "roxie")
    page = _map_page(browser, map_site, query="?view=map&at=37.7649,-122.4226,15.5")
    _wait_for_pins(page)
    _settle(page)
    _click_lnglat(page, ROXIE)
    page.wait_for_selector("#map-panel:not([hidden]) .event")
    head = page.inner_text("#map-panel .panel-head")
    assert "Roxie Theater" in head and f"{roxie} events" in head
    rows = page.locator("#map-panel .event")
    assert rows.count() == min(roxie, 30)
    assert set(rows.locator(".location-btn").all_inner_texts()) == {"Roxie Theater"}
    # Rows open the event like the list's (title links out; dates shown).
    assert rows.first.locator(".title a").get_attribute("href").startswith("https://example.com/e/")
    assert rows.first.locator(".td-date").count() == 1
    page.click("#map-panel [data-panel-all]")
    assert page.locator("#map-panel .event").count() == roxie
    # "Show in list": the venue filter, in the list.
    page.click("#map-panel [data-panel-list]")
    assert "venue=roxie" in page.url and "view=" not in page.url
    assert set(page.locator(".event .location-btn").all_inner_texts()) == {"Roxie Theater"}


def test_clicking_a_cluster_zooms_in(browser, map_site):
    page = _map_page(browser, map_site, query="?view=map&at=37.7750,-122.4200,9")
    _wait_for_pins(page)
    _settle(page)
    clusters = page.evaluate("agoraMap.queryRenderedFeatures({layers: ['clusters']}).length")
    assert clusters >= 1
    center = page.evaluate("agoraMap.queryRenderedFeatures({layers: ['clusters']})[0].geometry.coordinates")
    _click_lnglat(page, center)
    page.wait_for_function("agoraMap.getZoom() > 9.5", timeout=10000)


def test_approximate_pin_is_labelled(browser, map_site):
    page = _map_page(browser, map_site, query="?view=map&at=37.7764,-122.4347,16")
    _wait_for_pins(page)
    _settle(page)
    _click_lnglat(page, ALAMO)
    page.wait_for_selector("#map-panel:not([hidden]) .event")
    assert "Approximate location" in page.inner_text("#map-panel .panel-head")


def test_map_view_and_position_restore_from_the_url(browser, map_site):
    page = _map_page(browser, map_site, viewport=PHONE, touch=True,
                     query="?view=map&at=37.7650,-122.4200,14.5")
    _wait_for_pins(page)
    c = page.evaluate("[agoraMap.getCenter().lat, agoraMap.getCenter().lng, agoraMap.getZoom()]")
    assert abs(c[0] - 37.765) < 1e-3 and abs(c[1] + 122.42) < 1e-3 and abs(c[2] - 14.5) < 1e-6
    assert page.get_attribute('[data-view="map"]', "aria-pressed") == "true"
    # Moving the map rewrites ?at=.
    page.evaluate("agoraMap.jumpTo({center: [-122.41, 37.78], zoom: 13})")
    page.wait_for_function("decodeURIComponent(location.search).includes('at=37.7800,-122.4100,13')")
    # Garbled or out-of-area positions are ignored: the map fits the pins.
    for bad in ("at=nonsense", "at=48.85,2.35,12", "at=37.77,-122.42,99"):
        page = _map_page(browser, map_site, query="?view=map&" + bad)
        _wait_for_pins(page)
        assert page._agora_errors == []
        z = page.evaluate("agoraMap.getZoom()")
        assert 9 < z <= 14
        lng, lat = page.evaluate("[agoraMap.getCenter().lng, agoraMap.getCenter().lat]")
        assert -122.45 < lng < -122.39 and 37.76 < lat < 37.79


def test_neighborhood_filter_in_list_and_map(browser, map_site):
    events = [e for e in _map_events() if e["id"] != "started"]
    roxie = sum(1 for e in events if e["venue"] == "roxie")
    page = _map_page(browser, map_site)
    # Only neighborhoods with events, with counts.
    rows = page.locator("#hood-list label")
    assert rows.locator("span:not(.count)").all_text_contents() == ["Mission", "SoMa"]
    assert rows.locator(".count").all_text_contents() == [str(roxie), str(sum(
        1 for e in events if e["venue"] == "dawn-club"))]
    page.click("#hood-summary")
    page.check('#hood-list [data-hood="mission"]')
    assert "hood=mission" in page.url
    assert set(page.locator(".event .location-btn").all_inner_texts()) == {"Roxie Theater"}
    assert "Mission" in page.inner_text("#active-filters")
    assert page.inner_text("#hood-summary") == "Mission"
    # Alamo Square is SF but has no neighborhood: counted as unknown.
    assert "no known area or neighborhood" in page.inner_text("#meta")
    # OR with another neighborhood; with an area, neighborhoods narrow SF only.
    page.check('#hood-list [data-hood="soma"]')
    assert set(page.locator(".event .location-btn").all_inner_texts()) == {
        "Roxie Theater", "The Dawn Club", "The Dawn Club · Back Bar"}
    page.uncheck('#hood-list [data-hood="soma"]')
    page.click('#area-chips [data-area="eastbay"]')
    regions = set(page.locator(".event").evaluate_all("els => els.map(e => e.dataset.region)"))
    assert regions == {"sf", "eastbay"}
    page.click('#area-chips [data-area="eastbay"]')
    # The map shows the same: one pin, the Roxie.
    page.click('[data-view="map"]')
    _wait_for_pins(page)
    assert _pins(page) == {"roxie": roxie}
    assert "view=map" in page.url and "hood=mission" in page.url
    # Pill × clears it (and the badge counts it while it's on).
    page.set_viewport_size(PHONE)
    assert page.inner_text("#filters-count") == "1"
    page.click('#active-filters [data-clear="hood"]')
    page.wait_for_function("document.getElementById('map-wrap').dataset.venues === '3'")
    assert "hood=" not in page.url
    page.set_viewport_size(DESKTOP)
    # A shared link restores it; Reset clears it.
    page = _map_page(browser, map_site, query="?hood=mission,soma,nowhere")
    assert page.is_checked('#hood-list [data-hood="mission"]') and page.is_checked('#hood-list [data-hood="soma"]')
    assert page.inner_text("#hood-summary") == "2 SF neighborhoods"
    assert len(page.locator("#active-filters .active-pill").all()) == 2
    page.click("#reset")
    assert "hood=" not in page.url and not page.is_checked('#hood-list [data-hood="mission"]')
    assert page.inner_text("#active-filters").strip() == ""
    assert page._agora_errors == []


def test_no_neighborhood_ui_without_neighborhoods(browser, site, legacy_site):
    for s in (site, legacy_site):
        page = _map_page(browser, s, query="?hood=mission")
        assert page.locator("#hood-dropdown").is_hidden() and "hood=" not in page.url
        assert page._agora_errors == []


def test_sf_chip_and_neighborhoods_replace_each_other(browser, map_site):
    # Neighborhoods narrow SF, so the SF chip and a neighborhood are never
    # both on: the chip would claim "all of SF" while the list shows less.
    page = _map_page(browser, map_site)
    page.click('#area-chips [data-area="sf"]')
    page.click("#hood-summary")
    page.check('#hood-list [data-hood="mission"]')
    assert page.get_attribute('#area-chips [data-area="sf"]', "aria-pressed") == "false"
    assert "hood=mission" in page.url and "area=" not in page.url
    assert set(page.locator(".event .location-btn").all_inner_texts()) == {"Roxie Theater"}
    page.click('#area-chips [data-area="sf"]')
    assert not page.is_checked('#hood-list [data-hood="mission"]') and "hood=" not in page.url
    assert page.inner_text("#hood-summary") == "SF neighborhoods"
    # A hand-made link with both: the neighborhoods win; other areas stay.
    page = _map_page(browser, map_site, query="?area=sf,eastbay&hood=mission")
    assert page.get_attribute('#area-chips [data-area="sf"]', "aria-pressed") == "false"
    assert page.get_attribute('#area-chips [data-area="eastbay"]', "aria-pressed") == "true"
    assert "area=eastbay" in page.url and "hood=mission" in page.url
    assert page._agora_errors == []


def test_map_goes_to_the_results_when_none_are_in_view(browser, map_site):
    # Opened over Berkeley (no pins there): kept, since the link asked for it.
    page = _map_page(browser, map_site, query="?view=map&at=37.8700,-122.2600,15")
    _wait_for_pins(page)
    _settle(page)
    assert abs(page.evaluate("agoraMap.getCenter().lat") - 37.87) < 1e-3
    # A filter change that leaves nothing in view: the map fits the results.
    page.fill("#search-input", "Busy day")
    page.wait_for_function("document.getElementById('map-wrap').dataset.venues === '1'")
    page.wait_for_function("Math.abs(agoraMap.getCenter().lng - (%f)) < 0.01" % DAWN[0], timeout=10000)
    # One that leaves a result in view keeps the position.
    _settle(page)
    page.evaluate("agoraMap.jumpTo({center: [-122.415, 37.775], zoom: 12})")
    page.fill("#search-input", "")
    page.wait_for_function("document.getElementById('map-wrap').dataset.venues === '3'")
    page.wait_for_timeout(300)
    c = page.evaluate("[agoraMap.getCenter().lng, agoraMap.getZoom()]")
    assert abs(c[0] + 122.415) < 1e-6 and c[1] == 12
    # Nothing matches at all: say so (not "no map location").
    page.fill("#search-input", "zzzqqqxx")
    page.wait_for_function("document.getElementById('map-wrap').dataset.venues === '0'")
    assert "No events match" in page.inner_text("#map-status")
    assert page._agora_errors == []


def test_map_and_neighborhoods_are_beta_only(browser, map_site):
    # Outside the beta: no toggle, no neighborhood filter, links ignored,
    # MapLibre never requested.
    page = _map_page(browser, map_site, query="?view=map&at=37.77,-122.42,13&hood=mission", beta=False)
    assert page.locator("#view-toggle").is_hidden() and page.locator("#hood-dropdown").is_hidden()
    assert page.locator("#map-wrap").is_hidden() and _rendered(page) > 0
    assert "view=" not in page.url and "hood=" not in page.url
    assert not any("maplibre" in u for u in page._agora_requests)
    assert page._agora_errors == []
    # ?beta=1 (where frontend/beta/ redirects) lets the browser in.
    page = _map_page(browser, map_site, query="?beta=1&view=map", beta=False)
    _wait_for_pins(page)
    assert page.locator("#view-toggle").is_visible() and page.locator("#hood-dropdown").is_visible()


def test_beta_switch(browser, map_site):
    # A member opening the plain address: beta starts off, with a switch.
    page = _map_page(browser, map_site, beta="member")
    switch = page.locator(".ac-beta")
    assert switch.is_visible() and switch.get_attribute("aria-checked") == "false"
    assert page.locator("#view-toggle").is_hidden() and page.locator("#plan").is_hidden()
    # On: the page reloads with beta features. (The list view's address drops
    # beta=1 again, so a shared search link doesn't let people into the beta.)
    switch.click()
    page.wait_for_selector('.ac-beta[aria-checked="true"]')
    page.wait_for_selector(".event")
    assert page.locator("#view-toggle").is_visible() and page.locator("#plan").is_visible()
    # A reload keeps it on (same visit), even without ?beta=1.
    page.goto(map_site)
    page.wait_for_selector(".event")
    assert page.locator("#view-toggle").is_visible()
    # Off again: features gone, beta=1 dropped from the address.
    page.click(".ac-beta")
    page.wait_for_selector('.ac-beta[aria-checked="false"]')
    page.wait_for_selector(".event")
    assert page.locator("#view-toggle").is_hidden() and "beta=" not in page.url
    # A new tab on the plain address starts with beta off.
    tab = page.context.new_page()
    tab.goto(map_site)
    tab.wait_for_selector(".event")
    assert tab.locator('.ac-beta[aria-checked="false"]').is_visible()
    # Not a member: no switch at all.
    page = _map_page(browser, map_site, beta=False)
    assert page.locator(".ac-beta").count() == 0
    assert page._agora_errors == []


def test_shared_map_and_neighborhood_views_go_through_beta(browser, map_site):
    page = _map_page(browser, map_site, query="?hood=mission")
    # The address bar keeps beta=1, so a link copied from it works too.
    assert "beta=1" in page.url
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.click("#copy-link")
    link = page.evaluate("navigator.clipboard.readText()")
    assert link == map_site + "beta/?hood=mission"
    page = _map_page(browser, map_site, query="?type=talk")
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.click("#copy-link")
    assert page.evaluate("navigator.clipboard.readText()") == map_site + "?type=talk"


def test_day_strip_shows_the_month_on_every_day(browser, site):
    page = _open(browser, site, PHONE, touch=True)
    btns = page.locator("#day-strip .day-btn")
    keys = [btns.nth(i).get_attribute("data-day") for i in range(btns.count())]
    mons = page.locator("#day-strip .day-btn .mon").all_inner_texts()
    assert len(mons) == len(keys) and all(m.strip() for m in mons)
    # The first day of each new month (after the strip's first) is marked.
    changes = [k for prev, k in zip(keys, keys[1:]) if prev[:7] != k[:7]]
    marked = [btns.nth(i).get_attribute("data-day") for i in range(btns.count())
              if "new-month" in btns.nth(i).get_attribute("class")]
    assert marked == changes
