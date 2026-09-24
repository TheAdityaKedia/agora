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
