"""End-to-end check of the canvas UI in headless Chromium.

Starts the API locally (moto, snapshots from frontend/events.json) and a
static server for frontend/, then plays two friends in separate browser
contexts: Adi makes a canvas on the main page and adds events in canvas
mode; Sam opens the link, votes and comments; Adi sees it, removes + undoes,
picks the plan, adds a custom item. Fails on any page error.

    python canvas/scripts/e2e_frontend.py [--shots DIR]

Needs `playwright` (pip) and a Chromium (`playwright install chromium`, or
CHROMIUM_PATH=/path/to/chrome).
"""
import argparse
import contextlib
import functools
import os
import re
import http.server
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
API_PORT, WEB_PORT = 8787, 8765
API = f"http://localhost:{API_PORT}"
WEB = f"http://localhost:{WEB_PORT}"


@contextlib.contextmanager
def servers():
    api = subprocess.Popen([sys.executable, str(ROOT / "canvas/scripts/local_server.py"),
                            "--moto", "--port", str(API_PORT)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    handler = functools.partial(Quiet, directory=str(ROOT / "frontend"))
    web = http.server.ThreadingHTTPServer(("127.0.0.1", WEB_PORT), handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(API + "/canvases/" + "A" * 22)
            except urllib.error.HTTPError:
                break  # 404: the API is up
            except OSError:
                time.sleep(0.2)
        yield
    finally:
        web.shutdown()
        api.terminate()


def hermetic(ctx):
    """Stub every non-local request (event thumbnails) so the run doesn't
    depend on the network."""
    ctx.route(re.compile(r"^https?://(?!localhost[:/])"), lambda route: route.fulfill(status=204))
    return ctx


def watch(page, errors):
    page.on("pageerror", lambda e: errors.append(f"{page.url}: {e}"))
    page.on("console", lambda m: m.type == "error" and errors.append(f"{page.url}: console {m.text}"))


def answer_dialog(page, **values):
    dlg = page.locator("dialog.ac-dialog[open]")
    expect(dlg).to_be_visible()
    title = dlg.locator("h2").inner_text()
    for name, value in values.items():
        dlg.locator(f'[name="{name}"]').fill(value)
    dlg.locator('button[type="submit"]').click()
    # Another dialog (the name prompt) may open straight after this one.
    expect(page.locator("dialog.ac-dialog[open] h2", has_text=title)).to_have_count(0)


def run(shots):
    errors = []
    with servers(), sync_playwright() as p:
        # CHROMIUM_PATH: use an existing Chromium when the pip package's pinned
        # browser build isn't installed.
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        phone = {"viewport": {"width": 390, "height": 844}, "has_touch": True}

        # --- Adi: main page → Plan with friends → canvas mode → add two events
        adi = hermetic(browser.new_context(**phone))
        a = adi.new_page()
        watch(a, errors)
        a.goto(f"{WEB}/?api={API}")
        expect(a.locator(".event").first).to_be_visible()
        a.click("#plan-btn")
        answer_dialog(a, name="Adi & Sam hangout")
        answer_dialog(a, name="Adi")
        expect(a.locator("#canvas-tray")).to_contain_text("Adding to Adi & Sam hangout")
        assert "canvas=" in a.url, a.url
        adds = a.locator(".canvas-add")
        for i in range(2):
            adds.nth(i).click()
            expect(adds.nth(i)).to_have_attribute("aria-pressed", "true")
        expect(a.locator("#canvas-tray")).to_contain_text("2 options")
        if shots:
            a.screenshot(path=f"{shots}/1-index-canvas-mode.png")

        # Adding again from a fresh load: the remembered canvas comes back.
        a.goto(f"{WEB}/")
        expect(a.locator("#canvas-tray")).to_contain_text("2 options")
        expect(a.locator('.canvas-add[aria-pressed="true"]')).to_have_count(2)

        a.locator("#canvas-tray a").click()
        expect(a.locator(".item")).to_have_count(2)
        canvas_url = a.url
        a.locator(".item").first.locator('[data-act="vote"]').click()
        expect(a.locator(".item").first.locator(".voters")).to_have_text("Adi")

        # --- Sam: opens the shared link in their own browser
        sam = hermetic(browser.new_context(**phone))
        s = sam.new_page()
        watch(s, errors)
        s.goto(canvas_url + f"&api={API}")
        expect(s.locator(".item")).to_have_count(2)
        expect(s.locator(".cv-name")).to_have_text("Adi & Sam hangout")
        s.locator(".item").first.locator('[data-act="vote"]').click()
        answer_dialog(s, name="Sam")
        expect(s.locator(".item").first.locator(".voters")).to_have_text("Adi, Sam")
        s.locator(".item").first.locator('[data-act="comments"]').click()
        s.locator(".item").first.locator(".cmt-form input").fill("free after 7")
        s.locator(".item").first.locator(".cmt-form button").click()
        expect(s.locator(".item").first.locator(".cmt")).to_contain_text("free after 7")

        # --- Adi sees Sam's vote and comment; remove + undo; pick; custom item
        a.reload()
        first = a.locator(".item").first
        expect(first.locator(".voters")).to_have_text("Adi, Sam")
        expect(first.locator('[data-act="comments"]')).to_have_text("1 comment")
        a.locator(".item").nth(1).locator('[data-act="remove"]').click()
        expect(a.locator(".item")).to_have_count(1)
        a.locator(".ac-toast button", has_text="Undo").click()
        expect(a.locator(".item")).to_have_count(2)
        first.locator('[data-act="pick"]').click()
        expect(a.locator(".winner")).to_contain_text("The plan")
        a.click('[data-act="add-custom"]')
        answer_dialog(a, title="Dinner at Nopa", url="nopasf.com")
        expect(a.locator(".item")).to_have_count(3)
        expect(a.locator(".item", has_text="Dinner at Nopa").locator(".when")).to_contain_text("Any time")
        a.locator("details.fold summary", has_text="Activity").click()
        expect(a.locator("details.fold li").first).to_contain_text("Adi added “Dinner at Nopa”")
        if shots:
            a.screenshot(path=f"{shots}/2-canvas.png", full_page=True)

        # Sam's open page picks up Adi's changes by polling (forced here).
        s.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(s.locator(".item")).to_have_count(3)

        # A shared search link with canvas mode must not hang on "Loading…".
        a.goto(f"{WEB}/?q=jazz&canvas=" + canvas_url.split("c=")[1].split("&")[0])
        expect(a.locator("#canvas-tray")).to_contain_text("Adding to")
        expect(a.locator(".event").first).to_be_visible()

        # "Your canvases" lists it; Done ends canvas mode.
        a.goto(f"{WEB}/canvas.html")
        expect(a.locator(".mine li")).to_contain_text("Adi & Sam hangout")
        a.goto(f"{WEB}/")
        a.locator("#canvas-tray [data-tray-done]").click()
        expect(a.locator("#canvas-tray")).to_be_hidden()
        expect(a.locator(".canvas-add")).to_have_count(0)
        assert "canvas=" not in a.url, a.url

        # An unknown canvas explains itself.
        a.goto(f"{WEB}/canvas.html?c=" + "B" * 22)
        expect(a.locator(".problem")).to_contain_text("doesn't exist")
        browser.close()

    real = [e for e in errors if "404" not in e]  # the unknown-canvas fetch logs a 404
    if real:
        raise SystemExit("page errors:\n" + "\n".join(real))
    print("e2e OK")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", help="directory for screenshots")
    run(ap.parse_args().shots)
