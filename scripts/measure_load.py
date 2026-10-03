#!/usr/bin/env python3
"""Measure the site's first load (and, optionally, opening the map) in
headless Chromium on throttled phone and desktop profiles.

Serves a site directory gzipped, as GitHub Pages does. The default is
frontend/; pass a scratch copy from scripts/preview_site.py for a lean,
real-data manifest. It reports, as medians over --runs:

  manifest     events.json fully downloaded
  first row    the first .event in the DOM
  search ready the first search index built (MiniSearch addAllAsync done)
  blocked      total blocking time up to search ready: the part of each
               long task over 50 ms
  map pins     (--map) from tapping Map to the first frame with pins
               (the page's `agora:map-pins` performance mark)

The numbers come from outside the page (a MutationObserver, a wrapped
MiniSearch), so it can time an older page too, e.g. origin/main's. Only
"map pins" needs the page's mark.

Profiles follow Lighthouse's mobile throttling, applied over CDP:
  slow4g   390×844 phone, 1.6 Mbps down / 750 kbps up, 150 ms RTT, 4× CPU
  fast4g   390×844 phone, 9 Mbps down / 1.5 Mbps up, 60 ms RTT, 4× CPU
  desktop  1440×900, no throttling

    service/.venv/bin/python scripts/measure_load.py --root /tmp/agora-preview --map
    service/.venv/bin/python scripts/measure_load.py --root /tmp/agora-before --profiles fast4g

Map tiles come from the real OpenFreeMap server (throttled like the rest);
--no-tiles blocks them, so the map shows pins on a blank background.
"""
from __future__ import annotations

import argparse
import functools
import gzip
import http.server
import os
import statistics
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHROMIUM = "/opt/pw-browsers/chromium"

PROFILES = {
    "slow4g": {"viewport": {"width": 390, "height": 844}, "mobile": True,
               "net": (1.6e6, 750e3, 150), "cpu": 4},
    "fast4g": {"viewport": {"width": 390, "height": 844}, "mobile": True,
               "net": (9e6, 1.5e6, 60), "cpu": 4},
    "desktop": {"viewport": {"width": 1440, "height": 900}, "mobile": False,
                "net": None, "cpu": 1},
}

# Runs before the page's scripts: timestamps for the first row and the
# first finished search index, and a long-task log.
PROBE = """
(() => {
  // The map is in private beta (MAP_BETA_GATE): measure as a beta browser.
  try { localStorage.setItem("agora.canvas.beta", "true"); } catch (e) {}
  window.__agora = {firstRow: null, indexReady: null, longTasks: []};
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) __agora.longTasks.push([e.startTime, e.duration]);
  }).observe({type: "longtask", buffered: true});
  new MutationObserver((_, obs) => {
    if (document.querySelector(".event")) { __agora.firstRow = performance.now(); obs.disconnect(); }
  }).observe(document, {childList: true, subtree: true});
  let MS;
  Object.defineProperty(window, "MiniSearch", {
    configurable: true,
    get() { return MS; },
    set(v) {
      MS = v;
      const add = v.prototype.addAllAsync;
      v.prototype.addAllAsync = function (...args) {
        return add.apply(this, args).then((r) => {
          if (__agora.indexReady == null) __agora.indexReady = performance.now();
          return r;
        });
      };
    },
  });
})();
"""


class GzipHandler(http.server.SimpleHTTPRequestHandler):
    """Static files, gzip-encoded when the client accepts it (like Pages)."""
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".mjs": "text/javascript", ".js": "text/javascript",
                      ".json": "application/json", ".woff2": "font/woff2"}
    _cache: dict[str, bytes] = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            path = os.path.join(path, "index.html")
        if not os.path.isfile(path) or "gzip" not in self.headers.get("Accept-Encoding", ""):
            return super().do_GET()
        body = self._cache.get(path)
        if body is None:
            body = self._cache[path] = gzip.compress(Path(path).read_bytes(), 6)
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def serve(root: Path):
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(GzipHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def launch(p, exe: str | None):
    for path in ([exe] if exe else [DEFAULT_CHROMIUM if os.path.exists(DEFAULT_CHROMIUM) else None, None]):
        try:
            return p.chromium.launch(executable_path=path)
        except Exception as e:  # not installed at this path
            last = e
    raise SystemExit(f"no Chromium: {last}")


def measure_once(browser, url: str, prof: dict, with_map: bool, tiles: bool) -> dict:
    ctx = browser.new_context(viewport=prof["viewport"], is_mobile=prof["mobile"],
                              has_touch=prof["mobile"])
    if not tiles:
        ctx.route("https://tiles.openfreemap.org/**", lambda route: route.abort())
    ctx.add_init_script(PROBE)
    page = ctx.new_page()
    cdp = ctx.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
    if prof["net"]:
        down, up, rtt = prof["net"]
        cdp.send("Network.emulateNetworkConditions", {
            "offline": False, "latency": rtt,
            "downloadThroughput": down / 8, "uploadThroughput": up / 8})
    if prof["cpu"] > 1:
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": prof["cpu"]})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url, wait_until="commit")
    page.wait_for_function("window.__agora && __agora.indexReady != null", timeout=180_000, polling=100)
    out = page.evaluate("""() => {
        const a = __agora;
        const manifest = performance.getEntriesByType("resource")
            .find(r => r.name.endsWith("/events.json"));
        const blocked = a.longTasks.filter(([s]) => s < a.indexReady)
            .reduce((t, [, d]) => t + Math.max(0, d - 50), 0);
        return {manifest: manifest ? manifest.responseEnd : null,
                first_row: a.firstRow, search_ready: a.indexReady, blocked};
    }""")
    if with_map:
        if page.locator("#view-toggle").is_hidden():
            out["map_pins"] = None  # an old manifest: no map
        else:
            page.evaluate("() => { __agora.mapClick = performance.now();"
                          " document.querySelector('[data-view=map]').click(); }")
            page.wait_for_function("performance.getEntriesByName('agora:map-pins').length",
                                   timeout=180_000, polling=100)
            out["map_pins"] = page.evaluate(
                "performance.getEntriesByName('agora:map-pins')[0].startTime - __agora.mapClick")
    if errors:
        print("  page errors:", errors[:3])
    ctx.close()
    return out


def fmt(ms):
    return "—" if ms is None else f"{ms / 1000:.1f} s"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=ROOT / "frontend",
                    help="site directory to serve (default: frontend/)")
    ap.add_argument("--profiles", default="slow4g,fast4g,desktop")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--map", action="store_true", help="also time tapping Map to pins visible")
    ap.add_argument("--no-tiles", action="store_true", help="block map tiles")
    ap.add_argument("--chromium", help=f"Chromium binary (default {DEFAULT_CHROMIUM}, "
                                       "else Playwright's own)")
    args = ap.parse_args()

    server = serve(args.root)
    url = f"http://127.0.0.1:{server.server_port}/"
    cols = ["manifest", "first_row", "search_ready", "blocked"] + (["map_pins"] if args.map else [])
    rows = []
    with sync_playwright() as p:
        browser = launch(p, args.chromium)
        for name in args.profiles.split(","):
            runs = [measure_once(browser, url, PROFILES[name], args.map, not args.no_tiles)
                    for _ in range(args.runs)]
            med = {c: (statistics.median(r[c] for r in runs) if all(r.get(c) is not None for r in runs)
                       else None) for c in cols}
            rows.append((name, med))
            print(f"{name}: " + ", ".join(f"{c} {fmt(med[c])}" for c in cols), flush=True)
        browser.close()
    server.shutdown()

    print(f"\n{args.root} — median of {args.runs}\n")
    print("| | " + " | ".join(c.replace("_", " ") for c in cols) + " |")
    print("|---|" + "---:|" * len(cols))
    for name, med in rows:
        print(f"| {name} | " + " | ".join(fmt(med[c]) for c in cols) + " |")


if __name__ == "__main__":
    main()
