"""Shared headless-browser helpers for scrapers.

Several sources (Green Apple → Cloudflare, City Lights → Sucuri) sit behind a
WAF that a plain `requests.get` can't clear, so scrapers drive a headless
Chromium via Playwright and hand the rendered HTML to their own parsers.
"""
from contextlib import contextmanager

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
def browser_context():
    """Yield a configured Playwright browser context, tearing it down after."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            yield browser.new_context(
                user_agent=BROWSER_UA,
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
        finally:
            browser.close()


@contextmanager
def browser_session():
    """Yield an object with `.fresh_context()` that opens a new context per call.

    Some sources (SFJAZZ, Cloudflare-fronted) reject consecutive fetches on the
    same context — even with warmed cookies. But launching a full browser per
    fetch is slow. This helper amortizes the browser launch and lets callers
    open a fresh context (its own cookie jar + JS-challenge round) per URL:

        with browser_session() as sess:
            for url in urls:
                with sess.fresh_context() as ctx:
                    html = load_page_html(ctx, url)
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        class _Session:
            @contextmanager
            def fresh_context(self):
                ctx = browser.new_context(
                    user_agent=BROWSER_UA,
                    viewport={"width": 1280, "height": 800},
                    locale="en-US",
                )
                try:
                    yield ctx
                finally:
                    ctx.close()

        try:
            yield _Session()
        finally:
            browser.close()


def load_page_html(
    context,
    url: str,
    *,
    wait_until: str = "load",
    timeout: int = PAGE_READY_TIMEOUT_MS,
    settle_ms: int = 0,
) -> str:
    """Load `url` in a fresh page and return its HTML.

    `wait_until` picks the navigation completion signal ("load" for
    server-rendered pages, "networkidle" for JS/React-rendered ones).
    `settle_ms` adds a fixed pause after navigation for late-rendering content.
    Raises RateLimited on a 403/429 so callers can back off gracefully.
    On a navigation timeout we still return whatever loaded.
    """
    from playwright.sync_api import TimeoutError as PWTimeoutError

    page = context.new_page()
    try:
        response = None
        try:
            response = page.goto(url, wait_until=wait_until, timeout=timeout)
        except PWTimeoutError:
            pass
        if response is not None and response.status in (403, 429):
            raise RateLimited(url, response.status)
        if settle_ms:
            page.wait_for_timeout(settle_ms)
        return page.content()
    finally:
        page.close()
