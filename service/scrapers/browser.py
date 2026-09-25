"""Shared headless-browser helpers for scrapers.

Several sources (Green Apple → Cloudflare, City Lights → Sucuri) sit behind a
WAF that a plain `requests.get` can't clear, so scrapers drive a headless
Chromium via Playwright and hand the rendered HTML to their own parsers.
"""
import time
from contextlib import contextmanager
from typing import Callable, Optional

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# Max time to wait for a page (WAF challenge + render) before reading content.
PAGE_READY_TIMEOUT_MS = 20000


class RateLimited(Exception):
    """Raised when a source blocks us (429, or a WAF 403 challenge)."""
    def __init__(self, url: str, status: int):
        super().__init__(f"{status} for {url}")
        self.url = url
        self.status = status


@contextmanager
def browser_context(*, full_chromium: bool = False):
    """Yield a configured Playwright browser context, tearing it down after.

    `full_chromium=True` launches the full Chromium binary in (new) headless
    mode instead of Playwright's default `chrome-headless-shell` — a stripped
    build that Cloudflare's managed challenge detects. On a Mac host the shell
    happens to pass; inside our Linux container it gets stuck on "Just a
    moment..." from the second page on. The full binary passes in both, with
    no fingerprint spoofing. Opt-in per scraper (Green Apple) rather than the
    default, so sources that work today don't change behavior.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        launch_kwargs = {"channel": "chromium"} if full_chromium else {}
        browser = p.chromium.launch(headless=True, **launch_kwargs)
        try:
            yield new_browser_context(browser)
        finally:
            browser.close()


def new_browser_context(browser):
    """Open a context on `browser` with our standard UA/viewport/locale.

    Exposed so a scraper can swap in a fresh context (new cookie jar) mid-run —
    e.g. `context.browser` → `new_browser_context(...)` after a Cloudflare
    challenge, which scores per session rather than per IP.
    """
    return browser.new_context(
        user_agent=BROWSER_UA,
        viewport={"width": 1280, "height": 800},
        locale="en-US",
    )


@contextmanager
def browser_session(debug_log: Optional[Callable[[str], None]] = None):
    """Yield an object with `.fresh_context()` that opens a new context per call.

    Some sources (SFJAZZ, Cloudflare-fronted) reject consecutive fetches on the
    same context — even with warmed cookies. But launching a full browser per
    fetch is slow. This helper amortizes the browser launch and lets callers
    open a fresh context (its own cookie jar + JS-challenge round) per URL:

        with browser_session() as sess:
            for url in urls:
                with sess.fresh_context() as ctx:
                    html = load_page_html(ctx, url)

    Pass `debug_log` to see per-lifecycle timings for the browser and each
    context — useful when a scraper appears to hang and you can't tell whether
    it's stuck in launch, new_context, page load, or content extraction.
    """
    from playwright.sync_api import sync_playwright

    def log(msg: str) -> None:
        if debug_log:
            debug_log(msg)

    with sync_playwright() as p:
        log("browser: launching chromium")
        t0 = time.monotonic()
        browser = p.chromium.launch(headless=True)
        log(f"browser: launched in {time.monotonic() - t0:.2f}s")

        class _Session:
            @contextmanager
            def fresh_context(self):
                ct0 = time.monotonic()
                log("context: new_context")
                ctx = browser.new_context(
                    user_agent=BROWSER_UA,
                    viewport={"width": 1280, "height": 800},
                    locale="en-US",
                )
                log(f"context: opened in {time.monotonic() - ct0:.2f}s")
                try:
                    yield ctx
                finally:
                    cc0 = time.monotonic()
                    ctx.close()
                    log(f"context: closed in {time.monotonic() - cc0:.2f}s")

        try:
            yield _Session()
        finally:
            b0 = time.monotonic()
            log("browser: closing")
            browser.close()
            log(f"browser: closed in {time.monotonic() - b0:.2f}s")


def load_page_html(
    context,
    url: str,
    *,
    wait_until: str = "load",
    timeout: int = PAGE_READY_TIMEOUT_MS,
    settle_ms: int = 0,
    debug_log: Optional[Callable[[str], None]] = None,
) -> str:
    """Load `url` in a fresh page and return its HTML.

    `wait_until` picks the navigation completion signal ("load" for
    server-rendered pages, "networkidle" for JS/React-rendered ones).
    `settle_ms` adds a fixed pause after navigation for late-rendering content.
    Raises RateLimited on a 403/429 so callers can back off gracefully.
    On a navigation timeout we still return whatever loaded.

    Pass `debug_log` to trace sub-steps (page open, goto start/end with status,
    settle, content extraction, and every top-level HTTP response) — useful for
    diagnosing hangs on WAF-fronted or slow-rendering sources.
    """
    from playwright.sync_api import TimeoutError as PWTimeoutError

    def log(msg: str) -> None:
        if debug_log:
            debug_log(msg)

    log("page: new_page")
    t0 = time.monotonic()
    page = context.new_page()
    log(f"page: opened in {time.monotonic() - t0:.2f}s")

    if debug_log:
        # Response listener catches EVERY resource (main doc, XHR, image, etc.).
        # For a WAF-guarded page this is where a hanging challenge redirect
        # would surface as a chain of 403 → 200 responses on the main URL.
        def _on_response(resp):
            try:
                if resp.request.resource_type in ("document", "xhr", "fetch"):
                    log(f"  ← {resp.status} {resp.request.resource_type} {resp.url}")
            except Exception:
                pass
        page.on("response", _on_response)

    try:
        response = None
        try:
            log(f"page: goto (wait_until={wait_until}, timeout={timeout}ms)")
            g0 = time.monotonic()
            response = page.goto(url, wait_until=wait_until, timeout=timeout)
            log(f"page: goto returned in {time.monotonic() - g0:.2f}s "
                f"(status={response.status if response else 'None'})")
        except PWTimeoutError as e:
            log(f"page: goto timed out after {time.monotonic() - g0:.2f}s ({type(e).__name__})")
        if response is not None and response.status in (403, 429):
            raise RateLimited(url, response.status)
        if settle_ms:
            log(f"page: settle {settle_ms}ms")
            s0 = time.monotonic()
            page.wait_for_timeout(settle_ms)
            log(f"page: settled in {time.monotonic() - s0:.2f}s")
        log("page: content()")
        c0 = time.monotonic()
        html = page.content()
        log(f"page: content returned in {time.monotonic() - c0:.2f}s ({len(html)} bytes)")
        return html
    finally:
        log("page: close")
        page.close()
