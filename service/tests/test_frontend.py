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
            "location": "The Dawn Club, San Francisco" if jazz else "261 Columbus Ave",
            "url": f"https://example.com/e/{i}",
            "description": "Doors at seven. Bring a friend and stay late.",
            "image_url": None,
            "sources": ["The Dawn Club"] if jazz else ["City Lights"],
            "types": [["performance", "concert"]] if jazz else [["talk"]],
            "topics": ["jazz"] if jazz else ["poetry"],
            "cost": "unknown",
            # Every 10th event has no known area.
            "region": None if i % 10 == 9 else ("sf" if jazz else "eastbay"),
            "venue": None,
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


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    shutil.copy(FRONTEND / "index.html", root / "index.html")
    shutil.copytree(FRONTEND / "vendor", root / "vendor")
    (root / "events.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "taxonomy": TAXONOMY,
        "regions": [{"id": "sf", "label": "San Francisco"}, {"id": "eastbay", "label": "East Bay"},
                    {"id": "northbay", "label": "North Bay"}],
        "venues": {},
        "events": _events(),
    }))
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
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
    # Shown even though the location ("The Dawn Club, San Francisco") names it.
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
    assert _rendered(page) > 0
    # Friday from 5pm, Saturday, Sunday — and nothing else.
    assert page.evaluate("""[...document.querySelectorAll('.event')].every(e => {
        const d = new Date(e.dataset.start), w = d.getDay();
        return w === 6 || w === 0 || (w === 5 && d.getHours() >= 17);
    })""")
    page.click("#preset-weekend")
    assert "dates=" not in page.url
    # Survives a reload as the symbolic preset.
    page = _open(browser, site, DESKTOP, query="?dates=weekend")
    assert "active" in page.get_attribute("#preset-weekend", "class")
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
