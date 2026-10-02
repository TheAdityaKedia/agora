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

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}
TZ = "America/Los_Angeles"
N_EVENTS = 400

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
    base = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=19, minute=0, second=0, microsecond=0)
    out = []
    for i in range(n):
        jazz = i % 2 == 0
        out.append({
            "id": f"ev{i}",
            "title": f"Jazz night {i}" if jazz else f"Poetry reading {i}",
            "start_time": (base + timedelta(days=i // 4, hours=i % 4)).isoformat(),
            "location": "The Dawn Club, San Francisco" if jazz else "City Lights Books",
            "url": f"https://example.com/e/{i}",
            "description": "Doors at seven. Bring a friend and stay late.",
            "image_url": None,
            "sources": ["The Dawn Club"] if jazz else ["City Lights"],
            "types": [["performance", "concert"]] if jazz else [["talk"]],
            "topics": ["jazz"] if jazz else ["poetry"],
            "cost": "unknown",
        })
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
    assert 0 < first < N_EVENTS
    for _ in range(40):
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(50)
        if _rendered(page) == N_EVENTS:
            break
    assert _rendered(page) == N_EVENTS


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
