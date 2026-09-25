"""Omnivore Books on Food events scraper (Shopify "event" products).

Omnivore (a Noe Valley / Glen Park cookbook store) sells its events as Shopify
products in an ``upcoming-events`` collection. The collection's
``products.json`` gives each event's title, handle, HTML description, and
image, but not its date: that lives only on the product page, in a line like
``Thursday, October 8 at 6:30 pm`` (no year) in
``.product-form--block--overline``. We fetch each product page for that line
and resolve it with scrapers/datetext.py (year chosen by weekday).
Titles flagged ``*OFF-SITE*`` are hosted elsewhere (the venue is only named in
prose), so they don't get the shop's address.
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrapers.base import RawEvent
from scrapers.datetext import parse_weekday_date
from scrapers.browser import BROWSER_UA


SOURCE = "omnivorebooks.com"
NAME = "Omnivore Books on Food"
SHOP_BASE = "https://omnivorebooks.myshopify.com"
COLLECTION_URL = f"{SHOP_BASE}/collections/upcoming-events"
ADDRESS = "Omnivore Books on Food, 3885a Cesar Chavez St, San Francisco, CA 94131"
OFF_SITE_LOCATION = "Off-site (see event page) · presented by Omnivore Books"
SF_TZ = ZoneInfo("America/Los_Angeles")
REQUEST_TIMEOUT = 25

def matches(url: str) -> bool:
    return "omnivorebooks" in url


def parse_date_line(html: str) -> str | None:
    """The product page's date line, or None. Pure."""
    el = BeautifulSoup(html, "html.parser").select_one(".product-form--block--overline")
    return el.get_text(" ", strip=True) if el else None


def _html_to_text(raw: str | None) -> str | None:
    if not raw:
        return None
    text = re.sub(r"\s+", " ", BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)).strip()
    return text or None


def event_from_product(product: dict, start_time: datetime) -> RawEvent:
    title = re.sub(r"\s+", " ", product.get("title") or "").strip()
    images = product.get("images") or []
    off_site = "off-site" in title.lower()
    return RawEvent(
        title=title,
        start_time=start_time,
        location=OFF_SITE_LOCATION if off_site else ADDRESS,
        url=f"{SHOP_BASE}/products/{product['handle']}",
        description=_html_to_text(product.get("body_html")),
        image_url=images[0].get("src") if images else None,
    )


def _get(url: str, **kw) -> requests.Response:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT, **kw)
    resp.raise_for_status()
    return resp


def scrape(url: str = COLLECTION_URL) -> list[RawEvent]:
    try:
        products = _get(f"{COLLECTION_URL}/products.json", params={"limit": 250}).json().get("products", [])
    except (requests.RequestException, ValueError) as e:
        print(f"[omnivore] collection fetch failed: {e}", flush=True)
        return []
    today = datetime.now(SF_TZ).date()
    events: list[RawEvent] = []
    for product in products:
        try:
            line = parse_date_line(_get(f"{SHOP_BASE}/products/{product['handle']}").text)
        except requests.RequestException as e:
            print(f"[omnivore] product fetch failed {product.get('handle')}: {e}", flush=True)
            continue
        start_time = parse_weekday_date(line, today) if line else None
        if start_time is None:
            print(f"[omnivore] no date for {product.get('handle')}: {line!r}", flush=True)
            continue
        events.append(event_from_product(product, start_time))
    print(f"[omnivore] done: {len(events)} of {len(products)} products dated", flush=True)
    return events
