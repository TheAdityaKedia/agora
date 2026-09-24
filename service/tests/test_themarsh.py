from pathlib import Path

from scrapers import themarsh

FIXTURE = Path(__file__).parent / "fixtures" / "marsh_show.html"


def test_parse_show_page_extracts_title_desc_image():
    title, desc, image = themarsh.parse_show_page(FIXTURE.read_text())
    assert title == "Alicia Dattner – Small Batch, Artisanal Comedy"  # " - The Marsh" stripped
    assert desc and len(desc) > 40
    assert image == "https://themarsh.org/wp-content/uploads/2026/08/Alicia-Dattner-Web-Banner.png"


def test_parse_show_page_drops_boxoffice_boilerplate():
    _, desc, _ = themarsh.parse_show_page(FIXTURE.read_text())
    assert "Box Office" not in desc and "Valencia Street" not in desc


def test_parse_show_page_skips_button_images():
    _, _, image = themarsh.parse_show_page(FIXTURE.read_text())
    assert "Button-" not in image


def _entry(title, slug, url):
    norms = {n for n in (themarsh._norm(title), themarsh._norm(slug)) if n}
    return (themarsh._title_tokens(title), norms, {"description": "d", "image_url": "i", "url": url})


def test_best_match_by_title_tokens():
    index = [
        _entry("Alicia Dattner – Small Batch, Artisanal Comedy", "alicia-dattner-small-batch-artisanal-comedy", "wp"),
        _entry("Paul Sussman – Tantrum Yoga", "paul-sussman-tantrum-yoga", "wp2"),
    ]
    # noisy Ludus title still matches its WP page
    m = themarsh._best_match("Alicia Dattner's Small Batch Artisanal Comedy", index)
    assert m and m["url"] == "wp"


def test_best_match_compressed_title_via_normalized_containment():
    # WP og:title/slug is compressed ("NotJustJazz"); calendar is spaced + year.
    index = [_entry("NotJustJazz", "notjustjazz", "wp-njj")]
    m = themarsh._best_match("Not Just Jazz 2026", index)
    assert m and m["url"] == "wp-njj"


def test_best_match_returns_none_below_threshold():
    index = [_entry("Paul Sussman – Tantrum Yoga", "paul-sussman-tantrum-yoga", "wp2")]
    assert themarsh._best_match("Completely Unrelated Salsa Night", index) is None


def test_matches():
    assert themarsh.matches("https://themarsh.org/")
    assert themarsh.matches("https://themarsh.ludus.com/calendar")
    assert not themarsh.matches("https://litquake.org")


def test_norm_contains_matches_and_rejects():
    # compressed recurring slug contains / is contained by the calendar title
    assert themarsh._norm_contains(themarsh._norm("Tell It On Tuesday 2026"),
                                    {themarsh._norm("tell-it-on-tuesday-at-the-marsh")})
    # different shows sharing only common words must NOT match
    assert not themarsh._norm_contains(themarsh._norm("LABA's Name Game"),
                                        {themarsh._norm("elissa-strauss-name-game")})


def test_sitemap_candidates_include_shows_and_root_pages(monkeypatch):
    xml = (
        "<urlset>"
        "<url><loc><![CDATA[https://themarsh.org/shows_and_events/tellitontuesday/tell-it-on-tuesday-at-the-marsh/]]></loc></url>"
        "<url><loc><![CDATA[https://themarsh.org/shows_and_events/marshstream/monday-night-marshstream-5-25/]]></loc></url>"
        "<url><loc><![CDATA[https://themarsh.org/our-loving-companions/]]></loc></url>"
        "<url><loc><![CDATA[https://themarsh.org/about/]]></loc></url>"
        "</urlset>"
    )
    monkeypatch.setattr(themarsh, "_fetch", lambda u, s: xml)
    cands = themarsh._sitemap_candidates(None)
    urls = [u for _, u in cands]
    assert any("tell-it-on-tuesday" in u for u in urls)
    # root-level show pages are candidates now (some shows live off /shows_and_events/)
    assert any("our-loving-companions" in u for u in urls)
    assert not any("marshstream" in u for u in urls)  # livestream archives excluded
    # /about/ is a harmless candidate but must never match a real show title
    def norms_for(slug):
        return next(n for n, u in cands if slug in u)
    assert not themarsh._norm_contains(themarsh._norm("Our Loving Companions (San Francisco)"),
                                        norms_for("/about/"))
    assert themarsh._norm_contains(themarsh._norm("Our Loving Companions (San Francisco)"),
                                    norms_for("our-loving-companions"))


def test_evergreen_truncates_edition_lineup():
    desc = "Tell it on Tuesday celebrates storytelling. Mark McGoldrick, Producer Artist Biography: Janel Wagner is a singer..."
    ever = themarsh._evergreen(desc)
    assert ever.startswith("Tell it on Tuesday celebrates")
    assert "Janel Wagner" not in ever and "Artist Biography" not in ever


def test_recurring_show_only_next_occurrence_gets_full_desc(monkeypatch):
    from datetime import datetime, timezone
    from scrapers import ludus
    from scrapers.base import RawEvent

    def ev(title, day):
        return RawEvent(title=title, start_time=datetime(2026, 10, day, 2, tzinfo=timezone.utc),
                        location="SF", url="ludus", description=None)
    # 3 occurrences, given out of order to check earliest-wins
    monkeypatch.setattr(ludus, "scrape_calendar",
                        lambda u, **kw: [ev("Tell It On Tuesday 2026", 20), ev("Tell It On Tuesday 2026", 6),
                                          ev("Tell It On Tuesday 2026", 13)])
    payload = {"description": "Series blurb here. Featuring tonight's guest Jane Doe and her band.",
               "image_url": "poster.jpg", "url": "wp"}
    monkeypatch.setattr(themarsh, "_build_wp_index", lambda session: [None])
    monkeypatch.setattr(themarsh, "_sitemap_candidates", lambda session: [])
    monkeypatch.setattr(themarsh, "_best_match", lambda title, index: payload)
    evs = sorted(themarsh.scrape(), key=lambda e: e.start_time)
    # all get poster + WP url
    assert all(e.image_url == "poster.jpg" and e.url == "wp" for e in evs)
    # earliest (Oct 6) gets full text; later ones get the evergreen blurb
    assert "Jane Doe" in evs[0].description
    assert all("Jane Doe" not in e.description for e in evs[1:])
    assert evs[1].description.startswith("Series blurb here.")


def test_scrape_skips_monday_night_marsh(monkeypatch):
    from datetime import datetime, timezone
    from scrapers import ludus
    from scrapers.base import RawEvent

    def ev(title):
        return RawEvent(title=title, start_time=datetime(2026, 10, 1, 2, tzinfo=timezone.utc),
                        location="San Francisco", url="u", description=None)
    monkeypatch.setattr(ludus, "scrape_calendar",
                        lambda u, **kw: [ev("Monday Night Marsh 2026"), ev("Not Just Jazz 2026")])
    monkeypatch.setattr(themarsh, "_build_wp_index", lambda session: [])
    monkeypatch.setattr(themarsh, "_sitemap_candidates", lambda session: [])
    titles = [e.title for e in themarsh.scrape()]
    assert "Monday Night Marsh 2026" not in titles
    assert "Not Just Jazz 2026" in titles
