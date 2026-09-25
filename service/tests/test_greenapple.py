from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.greenapple import parse, find_next_month_url, _first_of_month_from_url

FIXTURE = Path(__file__).parent / "fixtures" / "greenapple_events.html"


@pytest.fixture
def html():
    return FIXTURE.read_text()


def mock_scrape(html_content):
    """Helper: parse fixture HTML directly, bypassing the browser fetch."""
    return parse(html_content)


def test_scrape_returns_list(html):
    events = mock_scrape(html)
    assert isinstance(events, list)


def test_scrape_finds_all_events(html):
    events = mock_scrape(html)
    assert len(events) == 3


def test_event_has_required_fields(html):
    event = mock_scrape(html)[0]
    assert isinstance(event, RawEvent)
    assert event.title
    assert isinstance(event.start_time, datetime)
    assert event.url


def test_event_title(html):
    events = mock_scrape(html)
    assert events[0].title == '9th Ave: Christian John Wikane with Timothy "T.K." Hampton'


def test_event_start_time(html):
    events = mock_scrape(html)
    # 7:00pm San Francisco time, stored as tz-aware UTC
    assert events[0].start_time == datetime(2026, 5, 4, 19, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


def test_event_url_is_absolute(html):
    events = mock_scrape(html)
    for event in events:
        assert event.url.startswith("https://")


def test_event_location(html):
    events = mock_scrape(html)
    assert "1231 9th Ave" in events[0].location


def test_find_next_month_url_present():
    html = '<html><body><a href="/events/2026/10">Next Month</a></body></html>'
    assert find_next_month_url(html) == "https://greenapplebooks.com/events/2026/10"


def test_find_next_month_url_absent():
    html = '<html><body><a href="/events/2026/08">Previous Month</a></body></html>'
    assert find_next_month_url(html) is None


def test_first_of_month_from_url_parses_absolute():
    from datetime import date
    assert _first_of_month_from_url("https://greenapplebooks.com/events/2027/03") == date(2027, 3, 1)


def test_first_of_month_from_url_parses_relative():
    from datetime import date
    assert _first_of_month_from_url("/events/2026/12/") == date(2026, 12, 1)


def test_first_of_month_from_url_rejects_non_month_urls():
    assert _first_of_month_from_url("https://greenapplebooks.com/events") is None
    assert _first_of_month_from_url("https://greenapplebooks.com/event/2026-09-10/some-slug") is None


def test_parse_extracts_image_url_and_resolves_relative():
    """Green Apple emits site-relative <img src="/sites/...">; resolve to absolute."""
    sample = '''
    <div class="views-row">
      <img src="/sites/default/files/styles/large/public/image/2026/08/14/2.png?itok=X">
      <h3 class="event-list__title"><a href="/event/x">A Talk</a></h3>
      <div class="event-list__details--item"><span class="event-list__details--label">Date:</span>Mon, 10/12/2026</div>
      <div class="event-list__details--item"><span class="event-list__details--label">Time:</span>7:00pm</div>
    </div>
    '''
    events = parse(sample)
    assert len(events) == 1
    assert events[0].image_url == "https://greenapplebooks.com/sites/default/files/styles/large/public/image/2026/08/14/2.png?itok=X"


def test_parse_image_url_missing_is_none():
    sample = '''
    <div class="views-row">
      <h3 class="event-list__title"><a href="/event/x">A Talk</a></h3>
      <div class="event-list__details--item"><span class="event-list__details--label">Date:</span>Mon, 10/12/2026</div>
      <div class="event-list__details--item"><span class="event-list__details--label">Time:</span>7:00pm</div>
    </div>
    '''
    events = parse(sample)
    assert len(events) == 1
    assert events[0].image_url is None


# --- pagination walk + enrichment (no network) -----------------------------

from contextlib import contextmanager

from scrapers import greenapple
from scrapers.browser import RateLimited

FIXTURES = Path(__file__).parent / "fixtures"
DETAIL_FIXTURE = FIXTURES / "greenapple_detail_offsite.html"
INSTORE_FIXTURE = FIXTURES / "greenapple_detail_instore.html"
NOIMAGE_FIXTURE = FIXTURES / "greenapple_detail_noimage.html"

INSTORE_IMAGE = ("https://greenapplebooks.com/sites/default/files/styles/event_image/"
                 "public/image/2026/08/14/2.png?itok=2KIUt55l")
LISTING_IMAGE = ("https://greenapplebooks.com/sites/default/files/styles/large/"
                 "public/image/2026/08/14/2.png?itok=LF95Dfjl")
TEASER = "Join us on Thursday, September 3 at 7pm when we celebrate indie publisher..."


def _month_page(events_html: str, next_month: str | None) -> str:
    nxt = f'<a href="{next_month}">Next Month</a>' if next_month else ""
    return f"<html><body>{events_html}{nxt}</body></html>"


def _row(slug: str, date_str: str, *, address: bool = True,
         teaser: str | None = None, image: str | None = None) -> str:
    """A listing row. `address=False` mimics an "Offsite:" row, which ships no
    <address>. `teaser`/`image` mimic the listing's truncated body + thumbnail."""
    addr = '<address>1231 9th Ave., San Francisco</address>' if address else ""
    body = f'<div class="event-list__body">{teaser}</div>' if teaser else ""
    img = f'<img src="{image}">' if image else ""
    return f'''
    <div class="views-row">
      {img}
      <h3 class="event-list__title"><a href="/event/{slug}">Event {slug}</a></h3>
      {addr}
      <div class="event-list__details--item"><span class="event-list__details--label">Date:</span>{date_str}</div>
      <div class="event-list__details--item"><span class="event-list__details--label">Time:</span>7:00pm</div>
      {body}
    </div>'''


def _is_listing(url: str) -> bool:
    return "/events" in url


@pytest.fixture
def fake_browser(monkeypatch):
    """Route scrape()'s page loads to a `pages` callable; record sleeps + URLs.

    Returns a dict: set `state["pages"] = fn(url) -> html` in the test; read
    `state["fetched"]` / `state["sleeps"]` afterwards. `state["log"]` holds
    ("sleep", s) / ("fetch", url) in call order, to check pacing per fetch.
    """
    state = {"fetched": [], "sleeps": [], "log": [], "pages": None}

    class FakeCtx:
        browser = "browser"

    @contextmanager
    def fake_context(**kwargs):
        state["launch_kwargs"] = kwargs
        yield FakeCtx()

    def fake_new_context(browser):
        state["fresh_contexts"] = state.get("fresh_contexts", 0) + 1
        return FakeCtx()

    def fake_load(ctx, url):
        state["fetched"].append(url)
        state["log"].append(("fetch", url))
        return state["pages"](url)

    def fake_sleep(s):
        state["sleeps"].append(s)
        state["log"].append(("sleep", s))

    monkeypatch.setattr(greenapple, "browser_context", fake_context)
    monkeypatch.setattr(greenapple, "load_page_html", fake_load)
    monkeypatch.setattr(greenapple, "new_browser_context", fake_new_context)
    monkeypatch.setattr(greenapple.time, "sleep", fake_sleep)
    return state


def test_scrape_follows_next_month(fake_browser):
    pages = {
        "https://greenapplebooks.com/events":
            _month_page(_row("a", "Mon, 10/12/2026"), "/events/2026/11"),
        "https://greenapplebooks.com/events/2026/11":
            _month_page(_row("b", "Mon, 11/09/2026"), None),
    }
    fake_browser["pages"] = lambda u: pages.get(u, NOIMAGE_FIXTURE.read_text())
    events = greenapple.scrape("https://greenapplebooks.com/events")
    assert [e.title for e in events] == ["Event a", "Event b"]
    # listing walk first, then one detail page per event
    assert fake_browser["fetched"] == list(pages) + [
        "https://greenapplebooks.com/event/a", "https://greenapplebooks.com/event/b"]


def test_scrape_honors_robots_crawl_delay(fake_browser):
    """robots.txt says crawl-delay: 10 and the owner is OK with that pace.

    Every fetch after the first (listing months AND detail pages) must be
    immediately preceded by a CRAWL_DELAY_S sleep."""
    assert greenapple.CRAWL_DELAY_S >= 10
    fake_browser["pages"] = lambda u: (
        _month_page(_row("a", "Mon, 10/12/2026"), "/events/2026/11")
        if u.endswith("/events") else
        _month_page(_row("b", "Mon, 11/09/2026"), None) if _is_listing(u)
        else NOIMAGE_FIXTURE.read_text())
    greenapple.scrape("https://greenapplebooks.com/events")
    log = fake_browser["log"]
    fetch_idx = [i for i, (kind, _) in enumerate(log) if kind == "fetch"]
    assert len(fetch_idx) == 4                       # 2 months + 2 detail pages
    assert fetch_idx[0] == 0                         # no delay before the first
    for i in fetch_idx[1:]:
        assert log[i - 1] == ("sleep", greenapple.CRAWL_DELAY_S)
    assert fake_browser["sleeps"] == [greenapple.CRAWL_DELAY_S] * 3


def test_scrape_stops_after_consecutive_empty_months(fake_browser):
    def pages(url):
        if not _is_listing(url):
            return NOIMAGE_FIXTURE.read_text()
        n = len(fake_browser["fetched"])
        body = _row("a", "Mon, 10/12/2026") if n == 1 else ""
        return _month_page(body, f"/events/2027/{n:02d}")

    fake_browser["pages"] = pages
    events = greenapple.scrape("https://greenapplebooks.com/events")
    # page 1 has an event, pages 2 and 3 are empty → stop at MAX_EMPTY_MONTHS.
    listing_fetches = [u for u in fake_browser["fetched"] if _is_listing(u)]
    assert len(listing_fetches) == 1 + greenapple.MAX_EMPTY_MONTHS
    assert len(events) == 1


def test_scrape_stops_early_when_blocked(fake_browser):
    """A 403 surfaces as RateLimited and ends the walk, keeping prior pages —
    and skips the detail pass, which would only hit the same block."""
    def pages(url):
        if len(fake_browser["fetched"]) == 1:
            return _month_page(_row("a", "Mon, 10/12/2026", teaser=TEASER),
                               "/events/2026/11")
        raise RateLimited(url, 403)

    fake_browser["pages"] = pages
    events = greenapple.scrape("https://greenapplebooks.com/events")
    assert len(events) == 1
    assert events[0].description == TEASER
    assert not [u for u in fake_browser["fetched"] if "/event/" in u]


# --- detail-page parsing ----------------------------------------------------

def test_parse_detail_location_from_p_address():
    """Offsite events carry the venue as p.address on the detail page."""
    loc = greenapple.parse_detail_location(DETAIL_FIXTURE.read_text())
    assert loc == "Sydney Goldstein Theater, 275 Hayes St, San Francisco, CA 94102"


def test_parse_detail_location_omits_country_and_stray_commas():
    loc = greenapple.parse_detail_location(DETAIL_FIXTURE.read_text())
    assert "United States" not in loc
    assert ", ," not in loc


def test_parse_detail_location_absent_returns_none():
    assert greenapple.parse_detail_location("<html><body><p>no address</p></body></html>") is None


def test_parse_detail_extracts_full_body():
    """The detail body is the full text the listing truncates to ~200 chars."""
    desc = greenapple.parse_detail(INSTORE_FIXTURE.read_text())["description"]
    assert desc.startswith("Join us on Thursday, September 3 at 7pm when we celebrate "
                           "indie publisher Black Ocean’s 20th Anniversary")
    assert len(desc) > 2000
    assert "Zachary Schomburg is the author of seven books of poems" in desc
    assert desc.endswith("resonates with readers around the world.")


def test_parse_detail_keeps_paragraph_and_line_breaks():
    desc = greenapple.parse_detail(INSTORE_FIXTURE.read_text())["description"]
    # <p> → blank line; <br> inside a <p> → single newline
    assert "at 9th Ave!\n\nFree to Attend, Please RSVP\nOr Watch on YouTube Live" in desc
    assert "\n\nAbout the Readers\nCarrie Olivia Adams lives in Chicago" in desc
    # inline tags don't split words or leave template whitespace behind
    assert "Her books include The Book of Marys and Glaciers, Be the thing" in desc
    assert "  " not in desc and "\xa0" not in desc and "\n\n\n" not in desc


def test_parse_detail_strips_boilerplate_and_chrome():
    desc = greenapple.parse_detail(INSTORE_FIXTURE.read_text())["description"]
    assert "Accessibility" not in desc                  # store-logistics block
    assert "ground level" not in desc
    assert "face masks" not in desc                     # mask-policy line
    assert "Welcome to our new website" not in desc     # site-alert (also .aba-body)
    assert "9th Ave: Black Ocean 20th Anniversary Celebration" not in desc  # <h1>
    assert "Place:" not in desc and "Thu, 9/3/2026" not in desc  # sidebar
    assert "Free to Attend" in desc                     # cost signal is kept


def test_parse_detail_strips_accessibility_tail_inside_a_paragraph():
    """Sometimes the boilerplate is the tail of the real <p>, after <br><br>."""
    desc = greenapple.parse_detail(NOIMAGE_FIXTURE.read_text())["description"]
    assert desc.endswith("Join us as we celebrate the literary works of USF writers!")
    assert "Accessibility" not in desc and "stairs" not in desc
    assert "masks" not in desc


def test_parse_detail_image_prefers_event_image():
    assert greenapple.parse_detail(INSTORE_FIXTURE.read_text())["image_url"] == INSTORE_IMAGE


def test_parse_detail_no_event_image_is_none():
    """Logo and ADA badge are on every page; they are not event images."""
    assert greenapple.parse_detail(DETAIL_FIXTURE.read_text())["image_url"] is None
    assert greenapple.parse_detail(NOIMAGE_FIXTURE.read_text())["image_url"] is None


def test_parse_detail_og_image_fallback_skips_mangled_values():
    """og:image is split on the alt text's commas; take only a real file URL."""
    html = '''<html><head>
      <meta property="og:image" content="https://greenapplebooks.comThursday">
      <meta property="og:image" content="https://greenapplebooks.com/sites/default/files/image/2026/08/14/2.png">
    </head><body></body></html>'''
    assert greenapple.parse_detail(html)["image_url"] == \
        "https://greenapplebooks.com/sites/default/files/image/2026/08/14/2.png"
    logo_only = '''<html><head>
      <meta property="og:image" content="https://greenapplebooks.com/sites/default/files/2024-07/gab-logo.png">
      <meta property="og:image" content="https://greenapplebooks.comSep. 3. 7pm">
    </head><body></body></html>'''
    assert greenapple.parse_detail(logo_only)["image_url"] is None


def test_parse_detail_offsite_has_body_and_location():
    d = greenapple.parse_detail(DETAIL_FIXTURE.read_text())
    assert d["location"] == "Sydney Goldstein Theater, 275 Hayes St, San Francisco, CA 94102"
    assert d["description"].startswith("City Arts & Lectures presents Lindy West")
    assert "About the Event\nThrough her comedic approach" in d["description"]


def test_parse_detail_empty_page():
    assert greenapple.parse_detail("<html><body><p>nothing</p></body></html>") == \
        {"description": None, "location": None, "image_url": None}


# --- detail-page enrichment in scrape() ------------------------------------

def _scrape_with(fake_browser, rows_html: str, detail):
    """Scrape a one-month listing; `detail(url)` serves (or raises for) detail pages."""
    listing = _month_page(rows_html, None)
    fake_browser["pages"] = lambda u: listing if _is_listing(u) else detail(u)
    events = greenapple.scrape("https://greenapplebooks.com/events")
    return {e.title: e for e in events}


def test_scrape_fetches_detail_for_every_event(fake_browser):
    """Every event gets one detail fetch; location is filled only where missing."""
    by_title = _scrape_with(
        fake_browser,
        _row("offsite-x", "Mon, 10/12/2026", address=False)   # "Offsite:" row
        + _row("instore", "Tue, 10/13/2026"),
        lambda u: DETAIL_FIXTURE.read_text())
    assert by_title["Event offsite-x"].location == \
        "Sydney Goldstein Theater, 275 Hayes St, San Francisco, CA 94102"
    # the listing's own store address is not overwritten by the detail page's
    assert "1231 9th Ave" in by_title["Event instore"].location
    assert fake_browser["fetched"] == ["https://greenapplebooks.com/events",
                                       "https://greenapplebooks.com/event/offsite-x",
                                       "https://greenapplebooks.com/event/instore"]


def test_scrape_replaces_teaser_with_full_body_and_upgrades_image(fake_browser):
    by_title = _scrape_with(
        fake_browser,
        _row("a", "Mon, 10/12/2026", teaser=TEASER, image=LISTING_IMAGE),
        lambda u: INSTORE_FIXTURE.read_text())
    ev = by_title["Event a"]
    assert ev.description == greenapple.parse_detail(INSTORE_FIXTURE.read_text())["description"]
    assert len(ev.description) > 2000
    assert ev.image_url == INSTORE_IMAGE


def test_scrape_keeps_teaser_when_detail_body_is_shorter_or_empty(fake_browser):
    long_teaser = "x" * 400 + "..."            # longer than the 359-char noimage body
    by_title = _scrape_with(
        fake_browser,
        _row("short", "Mon, 10/12/2026", teaser=long_teaser)
        + _row("empty", "Tue, 10/13/2026", teaser=TEASER),
        lambda u: NOIMAGE_FIXTURE.read_text() if u.endswith("/short")
        else "<html><body><div class='event-details__info--body'> </div></body></html>")
    assert by_title["Event short"].description == long_teaser
    assert by_title["Event empty"].description == TEASER


def test_scrape_does_not_invent_or_drop_images(fake_browser):
    """No detail image → keep the listing's image, or None if it had none."""
    by_title = _scrape_with(
        fake_browser,
        _row("thumb", "Mon, 10/12/2026", image=LISTING_IMAGE)
        + _row("bare", "Tue, 10/13/2026"),
        lambda u: NOIMAGE_FIXTURE.read_text())
    assert by_title["Event thumb"].image_url == LISTING_IMAGE
    assert by_title["Event bare"].image_url is None


def test_scrape_caps_detail_fetches(fake_browser, monkeypatch):
    monkeypatch.setattr(greenapple, "MAX_DETAIL_FETCHES", 2)
    by_title = _scrape_with(
        fake_browser,
        "".join(_row(s, "Mon, 10/12/2026", teaser=TEASER) for s in ("a", "b", "c")),
        lambda u: INSTORE_FIXTURE.read_text())
    assert [u for u in fake_browser["fetched"] if not _is_listing(u)] == [
        "https://greenapplebooks.com/event/a", "https://greenapplebooks.com/event/b"]
    assert len(by_title) == 3                       # capped events are still kept
    assert by_title["Event c"].description == TEASER


def test_enrichment_survives_a_failing_detail_page(fake_browser):
    """A broken detail fetch leaves that event's listing data; run continues."""
    def detail(url):
        if url.endswith("/event/a"):
            raise ValueError("boom")
        return DETAIL_FIXTURE.read_text()

    by_title = _scrape_with(
        fake_browser,
        _row("a", "Mon, 10/12/2026", address=False, teaser=TEASER)
        + _row("b", "Tue, 10/13/2026", address=False),
        detail)
    assert by_title["Event a"].location is None
    assert by_title["Event a"].description == TEASER
    assert by_title["Event b"].location.startswith("Sydney Goldstein")


def test_persistent_block_during_details_stops_enrichment_keeps_events(fake_browser):
    """Blocked even after the fresh-context retry → stop the detail pass, but
    keep every event (already-enriched ones stay enriched)."""
    def detail(url):
        if url.endswith("/event/a"):
            return INSTORE_FIXTURE.read_text()
        raise RateLimited(url, 403)

    by_title = _scrape_with(
        fake_browser,
        "".join(_row(s, "Mon, 10/12/2026", teaser=TEASER) for s in ("a", "b", "c")),
        detail)
    assert len(by_title) == 3
    assert len(by_title["Event a"].description) > 2000
    assert by_title["Event b"].description == TEASER
    assert by_title["Event c"].description == TEASER
    # b tried twice (original + fresh context), then c is never attempted
    assert [u for u in fake_browser["fetched"] if not _is_listing(u)] == [
        "https://greenapplebooks.com/event/a",
        "https://greenapplebooks.com/event/b", "https://greenapplebooks.com/event/b"]
    assert fake_browser["fresh_contexts"] == 1


def test_detail_challenge_retried_in_fresh_context(fake_browser):
    """A single challenge on a detail page is absorbed by the fresh context."""
    attempts = {"n": 0}

    def detail(url):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RateLimited(url, 403)
        return INSTORE_FIXTURE.read_text()

    by_title = _scrape_with(fake_browser, _row("a", "Mon, 10/12/2026", teaser=TEASER), detail)
    assert len(by_title["Event a"].description) > 2000
    assert fake_browser["fresh_contexts"] == 1


# --- browser / Cloudflare handling ------------------------------------------

def test_scrape_uses_full_chromium(fake_browser):
    """The default headless shell is stuck on Cloudflare's challenge in Docker."""
    fake_browser["pages"] = lambda u: (_month_page(_row("a", "Mon, 10/12/2026"), None)
                                       if _is_listing(u) else NOIMAGE_FIXTURE.read_text())
    greenapple.scrape("https://greenapplebooks.com/events")
    assert fake_browser["launch_kwargs"] == {"full_chromium": True}


def test_challenge_retried_once_in_fresh_context(fake_browser):
    """Cloudflare scores per session: a 403 gets one retry on a new cookie jar."""
    attempts = {"n": 0}

    def pages(url):
        if url.endswith("/events"):
            return _month_page(_row("a", "Mon, 10/12/2026"), "/events/2026/11")
        if not _is_listing(url):
            return NOIMAGE_FIXTURE.read_text()
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RateLimited(url, 403)          # first try: challenged
        return _month_page(_row("b", "Mon, 11/09/2026"), None)

    fake_browser["pages"] = pages
    events = greenapple.scrape("https://greenapplebooks.com/events")
    assert [e.title for e in events] == ["Event a", "Event b"]
    assert fake_browser["fresh_contexts"] == 1


def test_persistent_block_still_stops(fake_browser):
    """If the fresh context is challenged too, stop — don't retry forever."""
    def pages(url):
        if url.endswith("/events"):
            return _month_page(_row("a", "Mon, 10/12/2026"), "/events/2026/11")
        raise RateLimited(url, 403)

    fake_browser["pages"] = pages
    events = greenapple.scrape("https://greenapplebooks.com/events")
    assert len(events) == 1
    assert fake_browser["fresh_contexts"] == 1
