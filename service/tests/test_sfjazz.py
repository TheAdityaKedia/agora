import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers import sfjazz

FIXTURE = Path(__file__).parent / "fixtures" / "sfjazz_ace.json"
UTC = ZoneInfo("UTC")


@pytest.fixture
def events():
    return sfjazz.parse_events(json.loads(FIXTURE.read_text()))


def test_matches():
    assert sfjazz.matches("https://www.sfjazz.org/calendar/")
    assert not sfjazz.matches("https://www.act-sf.org/")


def test_one_event_per_performance(events):
    # Cyrille Aimée plays two nights → two events (multi-night runs split by date)
    assert len(events) == 3
    assert all(isinstance(e, RawEvent) for e in events)
    aimee = [e for e in events if e.title == "Cyrille Aimée"]
    assert len(aimee) == 2
    assert len({e.start_time for e in aimee}) == 2


def test_time_built_from_display_strings_as_pacific(events):
    ev = next(e for e in events if e.title.startswith("Hiromi"))
    # 10/1/2026 9:30 PM Pacific (PDT, -07:00) → 10/2/2026 04:30 UTC
    assert ev.start_time.astimezone(UTC) == datetime(2026, 10, 2, 4, 30, tzinfo=UTC)
    assert ev.start_time.tzinfo is not None


def test_location_url_image_absolute(events):
    ev = events[0]
    assert ev.location.startswith("SFJAZZ Center — ")
    assert ev.url and ev.url.startswith("https://www.sfjazz.org/tickets/")
    assert ev.image_url and ev.image_url.startswith("https://www.sfjazz.org/media/")


def test_parse_start_bad_input():
    assert sfjazz._parse_start(None, "9:00 PM") is None
    assert sfjazz._parse_start("10/1/2026", "nope") is None


def test_skips_items_without_title_or_time():
    evs = sfjazz.parse_events([
        {"name": "", "eventDateString": "10/1/2026", "eventTimeString": "9:00 PM"},
        {"name": "No time", "eventDateString": "10/1/2026", "eventTimeString": None},
    ])
    assert evs == []


# --- detail pages (phase 2) -------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"
SHOW_HTML = (FIXTURES / "sfjazz_detail_show.html").read_text()      # concert: fiftyfifty bio + Personnel
SERIES_HTML = (FIXTURES / "sfjazz_detail_series.html").read_text()  # class series: wysiwyg blurb + pricing
FAMILY_HTML = (FIXTURES / "sfjazz_detail_family.html").read_text()  # family matinee: upsell + placeholder


def test_parse_detail_show_body_paragraphs_and_personnel():
    desc = sfjazz.parse_detail(SHOW_HTML)
    paras = desc.split("\n\n")
    assert len(paras) == 4
    assert paras[0].startswith("Pianist and composer Jahari Stampley has emerged")
    assert paras[1].endswith("including 2025’s What A Time.")
    # inline tags don't inject stray spaces before punctuation
    assert "D-Erania Stampley, and drummer Miguel Russell, Jahari" in paras[2]
    assert paras[3] == ("Personnel: Jahari Stampley (keyboards); "
                        "D-Erania Stampley (piano, alto saxophone); Miguel Russell (drums)")
    assert "  " not in desc and "\xa0" not in desc


def test_parse_detail_show_strips_chrome():
    desc = sfjazz.parse_detail(SHOW_HTML)
    for noise in ("Become a Member", "Donate", "Buy Tickets", "$34.00",
                  "You Might Also Enjoy", "Marcus Miller", "Box Office",
                  "cookies", "Art Tatum",  # pull quote isn't body copy
                  "Boundary-Pushing Pianist"):  # hero eyebrow
        assert noise not in desc, noise
    # body wins over og:description when present
    assert "saxophonsit" not in desc


def test_parse_detail_series_drops_labels_ctas_and_testimonial():
    desc = sfjazz.parse_detail(SERIES_HTML)
    paras = desc.split("\n\n")
    assert paras[0].startswith("In celebration of John Coltrane's centennial")
    assert paras[1].startswith("Live music featuring special guests")
    # pricing kept, with its <br> lines preserved
    assert paras[2] == ("$100 Members / $130 Public (Full 4-class series)\n"
                        "*Save when you purchase the entire series!*\n"
                        "$30 Members / $35 Public (per class)")
    for noise in ("ABOUT THIS SERIES", "PRICING", "BUY THE FULL SERIES",
                  "Discover Jazz Patron", "Class 1", "Buy Tickets", "Personnel"):
        assert noise not in desc, noise


def test_parse_detail_family_drops_upsell_and_placeholder():
    desc = sfjazz.parse_detail(FAMILY_HTML)
    assert desc.startswith("Bassist, arranger and bandleader Marcus Shelby highlights")
    assert "\n\n" not in desc
    for noise in ("PART OF THE", "BUY SERIES TICKETS", "COMING SOON",
                  "FAMILY-FRIENDLY CONCERTS PACKAGE", "save 10%"):
        assert noise not in desc, noise


def test_parse_detail_falls_back_to_og_description():
    html = ('<html><head><meta property="og:description" content="  A short '
            'summary.  "></head><body><main><div class="wysiwyg-item"><div '
            'class="rich-text"><p class="h5-style">PRICING</p></div></div>'
            '</main></body></html>')
    assert sfjazz.parse_detail(html) == "A short summary."


def test_parse_detail_none_when_empty():
    assert sfjazz.parse_detail("") is None
    assert sfjazz.parse_detail("<html><body><main><p>Buy Tickets</p></main>"
                               "<footer><div class='rich-text'><p>Box Office</p>"
                               "</div></footer></body></html>") is None


def test_parse_events_uses_subtitle_as_listing_description():
    [ev] = sfjazz.parse_events([{
        "name": "Hiromi: The Trio Project", "eventDateString": "10/9/2026",
        "eventTimeString": "7:30 PM", "synopsis": "",
        "subtitle": "<p>with James Genus and Simon Phillips&nbsp;</p>",
        "viewDetailCtaUrl": "/tickets/productions/26-27/hiromi-the-trio-project/",
    }])
    assert ev.description == "with James Genus and Simon Phillips"
    assert sfjazz._listing_description({"subtitle": "", "synopsis": ""}) is None


def test_detail_url_goes_to_staging_and_only_tickets_paths():
    assert (sfjazz._detail_url("https://www.sfjazz.org/tickets/productions/26-27/x/")
            == "https://sfjazz-redesign-stage.adagetech.net/tickets/productions/26-27/x/")
    assert sfjazz._detail_url("https://www.sfjazz.org/umbraco/whatever/") is None
    assert sfjazz._detail_url(None) is None


class FakeResp:
    def __init__(self, status=200, text="", payload=None):
        self.status_code, self.text, self._payload = status, text, payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise sfjazz.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    """Stands in for requests.Session: `routes` maps URL -> FakeResp or an
    exception to raise; the listing API is served from `items`."""

    def __init__(self, routes=None, items=None, default=None):
        self.routes, self.items, self.default = routes or {}, items, default
        self.headers = {}
        self.calls: list[str] = []

    def get(self, url, **kw):
        self.calls.append(url)
        if url == sfjazz.ACE_API:
            if isinstance(self.items, Exception):
                raise self.items
            return FakeResp(payload=self.items)
        r = self.routes.get(url, self.default)
        if isinstance(r, Exception):
            raise r
        return r or FakeResp(status=404)


def _item(name, day, slug, subtitle=""):
    return {"name": name, "eventDateString": f"10/{day}/2026", "eventTimeString": "7:30 PM",
            "location": "Miner Auditorium", "synopsis": "", "subtitle": subtitle,
            "viewDetailCtaUrl": f"/tickets/productions/26-27/{slug}/",
            "thumbnail": f"/media/{slug}.jpg"}


def _stg(slug):
    return f"{sfjazz.STAGING_BASE}/tickets/productions/26-27/{slug}/"


@pytest.fixture
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(sfjazz.time, "sleep", lambda s: calls.append(s))
    return calls


def _run_scrape(monkeypatch, session):
    monkeypatch.setattr(sfjazz.requests, "Session", lambda: session)
    return sfjazz.scrape()


def test_scrape_dedupes_pages_and_fills_every_performance(monkeypatch, sleeps):
    items = [_item("Chris Botti", d, "chris-botti") for d in (1, 2, 3)]
    items.append(_item("Hiromi", 4, "hiromi", subtitle="<p>with James Genus</p>"))
    session = FakeSession(items=items, routes={
        _stg("chris-botti"): FakeResp(text=SHOW_HTML),
        _stg("hiromi"): FakeResp(text=FAMILY_HTML),
    })
    events = _run_scrape(monkeypatch, session)

    assert len(events) == 4
    detail_calls = [u for u in session.calls if u != sfjazz.ACE_API]
    # 3 performances share one page -> exactly one fetch; all fetches hit staging
    assert detail_calls == [_stg("chris-botti"), _stg("hiromi")]
    assert all(u.startswith("https://sfjazz-redesign-stage.adagetech.net/tickets/")
               for u in detail_calls)
    assert not any("www.sfjazz.org" in u for u in session.calls)
    botti = [e for e in events if e.title == "Chris Botti"]
    assert all(e.description and e.description.startswith("Pianist and composer")
               for e in botti)
    # subtitle kept as the lead paragraph
    hiromi = next(e for e in events if e.title == "Hiromi")
    assert hiromi.description.startswith("with James Genus\n\nBassist, arranger")
    # user-facing links stay on production
    assert all(e.url.startswith("https://www.sfjazz.org/tickets/") for e in events)
    assert all(e.image_url.startswith("https://www.sfjazz.org/media/") for e in events)
    assert session.headers["User-Agent"] == sfjazz.BROWSER_UA
    # polite: one delay between the two fetches, none before the first
    assert sleeps == [sfjazz.DETAIL_DELAY_S]


def test_scrape_failed_page_is_isolated(monkeypatch, sleeps):
    items = [_item("A", 1, "a", subtitle="<p>Ages 2 - 3.5</p>"), _item("B", 2, "b"),
             _item("C", 3, "c"), _item("D", 4, "d")]
    session = FakeSession(items=items, routes={
        _stg("a"): FakeResp(status=500),
        _stg("b"): sfjazz.requests.Timeout("slow"),
        _stg("c"): FakeResp(text=SHOW_HTML),
        _stg("d"): FakeResp(text="<html><body><main></main></body></html>"),
    })
    events = {e.title: e for e in _run_scrape(monkeypatch, session)}
    assert len(events) == 4
    assert events["A"].description == "Ages 2 - 3.5"  # listing subtitle survives
    assert events["B"].description is None
    assert events["C"].description.startswith("Pianist and composer")
    assert events["D"].description is None  # page fetched but empty


def test_scrape_stops_enrichment_after_consecutive_failures(monkeypatch, sleeps):
    n = sfjazz.MAX_CONSECUTIVE_FAILURES + 3
    items = [_item(f"Show {i}", i, f"s{i}") for i in range(1, n + 1)]
    session = FakeSession(items=items, default=FakeResp(status=503))
    events = _run_scrape(monkeypatch, session)
    detail_calls = [u for u in session.calls if u != sfjazz.ACE_API]
    assert len(detail_calls) == sfjazz.MAX_CONSECUTIVE_FAILURES
    assert len(events) == n  # every event still returned
    assert all(e.description is None for e in events)


def test_consecutive_failure_counter_resets_on_success(monkeypatch, sleeps):
    monkeypatch.setattr(sfjazz, "MAX_CONSECUTIVE_FAILURES", 2)
    items = [_item(f"S{i}", i, f"s{i}") for i in range(1, 6)]
    session = FakeSession(items=items, routes={
        _stg("s1"): FakeResp(status=500), _stg("s2"): FakeResp(text=SHOW_HTML),
        _stg("s3"): FakeResp(status=500), _stg("s4"): FakeResp(text=SHOW_HTML),
        _stg("s5"): FakeResp(status=500),
    })
    _run_scrape(monkeypatch, session)
    assert len([u for u in session.calls if u != sfjazz.ACE_API]) == 5


def test_scrape_caps_detail_fetches(monkeypatch, sleeps, capsys):
    monkeypatch.setattr(sfjazz, "MAX_DETAIL_FETCHES", 2)
    items = [_item(f"S{i}", i, f"s{i}") for i in range(1, 5)]
    session = FakeSession(items=items, default=FakeResp(text=SHOW_HTML))
    events = _run_scrape(monkeypatch, session)
    detail_calls = [u for u in session.calls if u != sfjazz.ACE_API]
    assert detail_calls == [_stg("s1"), _stg("s2")]  # soonest first
    assert [bool(e.description) for e in events] == [True, True, False, False]
    assert "MAX_DETAIL_FETCHES" in capsys.readouterr().out


def test_scrape_listing_failure_returns_empty(monkeypatch, sleeps):
    session = FakeSession(items=sfjazz.requests.ConnectionError("down"))
    assert _run_scrape(monkeypatch, session) == []
    assert session.calls == [sfjazz.ACE_API]


def _page(body_ps: str = "", personnel_ps: str = "") -> str:
    """Minimal page in the real block markup (snippets copied from live pages)."""
    lineup = (f'<div class="fullwidthcta-content"><h2>Personnel</h2>'
              f'<div class="rich-text">{personnel_ps}</div></div>') if personnel_ps else ""
    return (f'<html><body><main><div class="wysiwyg-item"><div class="grid-item rich-text">'
            f'{body_ps}</div></div>{lineup}</main></body></html>')


BIO = '<p class="large">A real bio paragraph.</p>'


@pytest.mark.parametrize("personnel, expected", [
    # name unmarked; <br> tucked inside the role span (Matthew Whitaker)
    ('<p class="large">Matthew Whitaker <span class="light">keyboards<br/></span>Others TBA</p>',
     "Personnel: Matthew Whitaker (keyboards); Others TBA"),
    # first name unmarked, second in <strong> (Mark Lettieri & Purbayan Chatterjee)
    ('<p class="large">Mark Lettieri <span class="light">guitar</span><br/><strong>Purbayan '
     'Chatterjee</strong> <span class="light">sitar</span></p>',
     "Personnel: Mark Lettieri (guitar); Purbayan Chatterjee (sitar)"),
    # brand-red nested inside strong (Kenny Garrett)
    ('<p class="large"><strong><span class="brand-red">Kenny Garrett</span> </strong><span '
     'class="light">alto saxophone, soprano saxophone, flute</span> <br/>Others TBA </p>',
     "Personnel: Kenny Garrett (alto saxophone, soprano saxophone, flute); Others TBA"),
])
def test_personnel_markup_variants(personnel, expected):
    assert sfjazz.parse_detail(_page(BIO, personnel)) == f"A real bio paragraph.\n\n{expected}"


@pytest.mark.parametrize("personnel", [
    "<p>Check back soon for personnel information</p>",
    "<p>Others TBA</p>",
])
def test_placeholder_only_personnel_is_omitted(personnel):
    assert sfjazz.parse_detail(_page(BIO, personnel)) == "A real bio paragraph."


@pytest.mark.parametrize("noise", [
    "Please check back for more information about these performances.",
    "Save 10% when you purchase 5 or more concerts together",
    "TWO-TICKET PACKAGES AVAILABLE",
    "<strong>PART OF THE FAMILY-FRIENDLY CONCERT SERIES!</strong>",
])
def test_body_placeholders_and_upsells_dropped(noise):
    assert sfjazz.parse_detail(_page(BIO + f'<p class="large">{noise}</p>')) == "A real bio paragraph."


def test_purchase_link_lines_dropped_but_inline_links_kept():
    # real markup from Holly Bowling's page: package-purchase lines above the bio
    purchase = ('<p><span class="large"><a href="https://www.sfjazz.org/tickets/packages/'
                'flex-package-listing-page/?packageID=18"><span class="brand-red"><strong>'
                'Purchase</strong></span></a> › <strong>Nov 13: 7PM &amp; 8:30PM</strong>'
                '</span><br/><a class="large brand-red" href="https://www.sfjazz.org/tickets/'
                'packages/flex-package-listing-page/?packageID=19"><strong>Purchase</strong>'
                '</a><span class="large"> › <strong>Nov 14: 7PM &amp; 8:30PM</strong></span></p>')
    inline = ('<p class="large">Her run includes a <a href="/tickets/packages/x/">two-show '
              'package</a> for fans.</p>')
    assert (sfjazz.parse_detail(_page(purchase + BIO + inline))
            == "A real bio paragraph.\n\nHer run includes a two-show package for fans.")
