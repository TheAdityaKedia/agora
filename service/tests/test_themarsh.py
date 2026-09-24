from datetime import datetime, timezone
from pathlib import Path

from scrapers import themarsh, ludus
from scrapers.base import RawEvent

FIXTURE = Path(__file__).parent / "fixtures" / "marsh_show.html"


def test_matches():
    assert themarsh.matches("https://themarsh.org/")
    assert themarsh.matches("https://themarsh.ludus.com/calendar")
    assert not themarsh.matches("https://litquake.org")


def test_show_id_from_both_url_forms():
    # calendar shareUrl form
    assert themarsh._show_id("https://themarsh.ludus.com/show_page.php?show_id=200542218") == "200542218"
    # WP "Buy Tickets" link form
    assert themarsh._show_id("https://themarsh.ludus.com/200542218") == "200542218"
    assert themarsh._show_id("https://themarsh.ludus.com/donate.php") is None
    assert themarsh._show_id(None) is None


def test_parse_show_page_extracts_title_desc_image():
    title, desc, image = themarsh.parse_show_page(FIXTURE.read_text())
    assert title == "Alicia Dattner – Small Batch, Artisanal Comedy"
    assert desc and "Box Office" not in desc and "Valencia Street" not in desc
    assert image == "https://themarsh.org/wp-content/uploads/2026/08/Alicia-Dattner-Web-Banner.png"
    assert "Button-" not in image


def test_evergreen_truncates_edition_lineup():
    desc = "Tell it on Tuesday celebrates storytelling. Mark McGoldrick, Producer Artist Biography: Janel Wagner is a singer..."
    ever = themarsh._evergreen(desc)
    assert ever.startswith("Tell it on Tuesday celebrates")
    assert "Janel Wagner" not in ever and "Artist Biography" not in ever


def test_sitemap_pages_orders_by_lastmod_and_excludes_marshstream(monkeypatch):
    xml = (
        "<urlset>"
        "<url><loc><![CDATA[https://themarsh.org/shows_and_events/old-show/]]></loc>"
        "<lastmod><![CDATA[2025-01-01T00:00:00+00:00]]></lastmod></url>"
        "<url><loc><![CDATA[https://themarsh.org/our-loving-companions/]]></loc>"
        "<lastmod><![CDATA[2026-09-24T00:00:00+00:00]]></lastmod></url>"
        "<url><loc><![CDATA[https://themarsh.org/shows_and_events/marshstream/jazz-jam/]]></loc>"
        "<lastmod><![CDATA[2026-09-01T00:00:00+00:00]]></lastmod></url>"
        "</urlset>"
    )
    monkeypatch.setattr(themarsh, "_fetch", lambda u, s: xml if "sitemap" in u else "")
    pages = themarsh._sitemap_pages(None)
    assert "https://themarsh.org/our-loving-companions/" in pages
    assert not any("marshstream" in p for p in pages)      # livestream archives excluded
    # newest-modified first
    assert pages.index("https://themarsh.org/our-loving-companions/") < \
        pages.index("https://themarsh.org/shows_and_events/old-show/")


def test_build_show_index_maps_ids_and_stops_early(monkeypatch):
    pages = ["https://themarsh.org/a/", "https://themarsh.org/b/", "https://themarsh.org/never/"]
    bodies = {
        "https://themarsh.org/a/": "<meta property='og:title' content='A - The Marsh'>"
                                   "<div class='entry-content'><p>" + "x" * 40 + "</p></div>"
                                   "<a href='https://themarsh.ludus.com/111'>tix</a>",
        "https://themarsh.org/b/": "<meta property='og:title' content='B - The Marsh'>"
                                   "<div class='entry-content'><p>" + "y" * 40 + "</p></div>"
                                   "<a href='https://themarsh.ludus.com/222'>tix</a>",
    }
    fetched = []
    def fake_fetch(u, s):
        fetched.append(u)
        return bodies.get(u, "")
    monkeypatch.setattr(themarsh, "_sitemap_pages", lambda s: pages)
    monkeypatch.setattr(themarsh, "_fetch", fake_fetch)
    monkeypatch.setattr(themarsh, "DETAIL_WORKERS", 1)  # per-page chunks so early-stop is observable
    id_index, _ = themarsh._scan_show_pages(None, shows=[("111", "A"), ("222", "B")])
    assert set(id_index) == {"111", "222"}
    assert id_index["111"]["url"] == "https://themarsh.org/a/"
    # stopped once both ids resolved — never fetched the third page
    assert "https://themarsh.org/never/" not in fetched


def _ludus_ev(title, day, sid):
    return RawEvent(title=title, start_time=datetime(2026, 10, day, 2, tzinfo=timezone.utc),
                    location="SF", url=f"https://themarsh.ludus.com/show_page.php?show_id={sid}",
                    description=None)


def test_scrape_joins_by_show_id_with_next_occurrence(monkeypatch):
    # two showtimes of one show (same id), out of order
    monkeypatch.setattr(ludus, "scrape_calendar",
                        lambda u, **kw: [_ludus_ev("Tell It On Tuesday", 20, "555"),
                                          _ludus_ev("Tell It On Tuesday", 6, "555"),
                                          _ludus_ev("Monday Night Marsh", 7, "999")])
    payload = {"description": "Series blurb. Featuring guest Jane Doe.",
               "image_url": "poster.jpg", "url": "https://themarsh.org/tiot/"}
    monkeypatch.setattr(themarsh, "_scan_show_pages", lambda s, shows: ({"555": payload}, []))
    evs = sorted(themarsh.scrape(), key=lambda e: e.start_time)
    # Monday Night Marsh filtered out
    assert all("Monday Night Marsh" not in e.title for e in evs)
    # both showtimes get poster + WP url; earliest gets full text, later the blurb
    assert all(e.image_url == "poster.jpg" and e.url == "https://themarsh.org/tiot/" for e in evs)
    assert "Jane Doe" in evs[0].description
    assert "Jane Doe" not in evs[1].description


def test_scrape_title_fallback_when_no_show_id_link(monkeypatch):
    # Not Just Jazz: page embeds no Ludus id, so id_index misses it — resolve by
    # slug/title containment ("notjustjazz" ⊂ "notjustjazz2026").
    monkeypatch.setattr(ludus, "scrape_calendar",
                        lambda u, **kw: [_ludus_ev("Not Just Jazz 2026", 8, "515467")])
    njj = {"description": "Weekly jazz.", "image_url": "njj.jpg", "url": "https://themarsh.org/shows_and_events/notjustjazz/"}
    title_index = [({themarsh._norm("notjustjazz")}, njj)]
    monkeypatch.setattr(themarsh, "_scan_show_pages", lambda s, shows: ({}, title_index))
    ev = themarsh.scrape()[0]
    assert ev.description == "Weekly jazz." and ev.image_url == "njj.jpg"
