"""Zyte API fetch backend — the LAST resort for WAF-blocked sources.

No scraper uses this today. Work the ladder in CONTRIBUTING.md → "When to give
up" first: an un-fronted origin API (SFJAZZ) or full Chromium + crawl-delay +
fresh context on challenge (Green Apple) are free and fixed the two sources that
looked hopeless. Reach for Zyte only for a source that beats all of those. Zyte
API is a hosted fetch service: POST a URL, it runs the request through rotating
proxies plus its own anti-ban stack, and returns the response body.

This module is a thin, *optional* seam. `fetch_html()` is a drop-in replacement
for `browser.load_page_html()` that scrapers reach for only when a key is
present:

    if zyte.is_configured():
        html = zyte.fetch_html(url)
    else:
        html = load_page_html(context, url)   # existing path

Keeping it env-gated means local runs, CI, and the test suite never touch the
network or spend credits, and a source degrades to its current behavior (usually
"blocked, 0 events") rather than breaking when no key is set.

Cost model — this is metered, so treat it as expensive:
  - `render=False` (`httpResponseBody`) is the cheap tier, but it does NOT clear
    a real Cloudflare challenge; Green Apple returns a 520 "Website Ban".
  - `render=True` (`browserHtml`) runs a full browser with the anti-ban stack.
    It is what actually works, and costs several times more (~20s per call).
Only route the genuinely-blocked fetches through here. Never put a
high-volume enrichment loop (e.g. SFPL's 800+ detail pages) behind it.
"""
from __future__ import annotations

import base64
import os
import time

import requests

from scrapers.browser import RateLimited


API_URL = "https://api.zyte.com/v1/extract"
ENV_KEY = "ZYTE_API_KEY"

# A browser-render call takes ~15-25s; the ban retry can double that.
REQUEST_TIMEOUT = 180
# Zyte answers a proxy-exhausted fetch with 520 "Website Ban". It is transient
# (a different IP may get through), so retry a couple of times before giving up.
BAN_STATUS = 520
MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = 3.0


class ZyteNotConfigured(RuntimeError):
    """Raised when a Zyte fetch is attempted with no API key in the env."""


def api_key() -> str | None:
    return os.environ.get(ENV_KEY) or None


def is_configured() -> bool:
    """True when a Zyte API key is available, so callers can pick a backend."""
    return api_key() is not None


def _extract_html(payload: dict) -> str:
    """Pull the HTML out of a Zyte response (browserHtml or base64 raw body)."""
    if payload.get("browserHtml"):
        return payload["browserHtml"]
    raw = payload.get("httpResponseBody")
    if raw:
        return base64.b64decode(raw).decode("utf-8", "replace")
    return ""


def fetch_html(
    url: str,
    *,
    render: bool = True,
    geolocation: str | None = None,
    timeout: int = REQUEST_TIMEOUT,
    attempts: int = MAX_ATTEMPTS,
    log=None,
) -> str:
    """Fetch `url` through Zyte API and return its HTML.

    `render=True` asks for `browserHtml` (full browser + anti-ban — the mode
    that actually defeats Cloudflare); `render=False` asks for the cheaper raw
    `httpResponseBody`.

    Raises `RateLimited` when Zyte can't get a ban-free response after
    `attempts` tries — deliberately the same exception `browser.load_page_html`
    raises, so a scraper's existing "blocked, stop early" handling applies
    unchanged regardless of which backend it used.
    """
    key = api_key()
    if not key:
        raise ZyteNotConfigured(f"{ENV_KEY} is not set")

    body: dict = {"url": url}
    if render:
        body["browserHtml"] = True
    else:
        body["httpResponseBody"] = True
    if geolocation:
        body["geolocation"] = geolocation

    last_status = BAN_STATUS
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(API_URL, auth=(key, ""), json=body, timeout=timeout)
        except requests.RequestException as e:
            if log:
                log(f"zyte: {type(e).__name__} on attempt {attempt}/{attempts} for {url}")
            last_status = 0
            if attempt == attempts:
                raise RateLimited(url, last_status) from e
            time.sleep(RETRY_BACKOFF_S * attempt)
            continue

        if resp.status_code == 200:
            return _extract_html(resp.json())

        last_status = resp.status_code
        # 520 = "Website Ban" (proxies exhausted); 429 = our own Zyte-account
        # rate limit. Both are worth another try on a fresh IP / after a pause.
        if resp.status_code in (BAN_STATUS, 429) and attempt < attempts:
            if log:
                log(f"zyte: HTTP {resp.status_code} on attempt {attempt}/{attempts}, retrying")
            time.sleep(RETRY_BACKOFF_S * attempt)
            continue
        if log:
            log(f"zyte: HTTP {resp.status_code} for {url}: {resp.text[:200]}")
        break

    raise RateLimited(url, last_status)
