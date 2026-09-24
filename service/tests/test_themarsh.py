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


def test_best_match_by_title_tokens():
    index = [
        (themarsh._title_tokens("Alicia Dattner – Small Batch, Artisanal Comedy"),
         {"description": "d", "image_url": "i", "url": "wp"}),
        (themarsh._title_tokens("Paul Sussman – Tantrum Yoga"),
         {"description": "d2", "image_url": "i2", "url": "wp2"}),
    ]
    # noisy Ludus title still matches its WP page
    m = themarsh._best_match("Alicia Dattner's Small Batch Artisanal Comedy", index)
    assert m and m["url"] == "wp"


def test_best_match_returns_none_below_threshold():
    index = [(themarsh._title_tokens("Paul Sussman – Tantrum Yoga"), {"url": "wp2"})]
    assert themarsh._best_match("Completely Unrelated Jazz Night", index) is None


def test_matches():
    assert themarsh.matches("https://themarsh.org/")
    assert themarsh.matches("https://themarsh.ludus.com/calendar")
    assert not themarsh.matches("https://litquake.org")
