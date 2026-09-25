from pathlib import Path

from scrapers import clios

FIXTURE = Path(__file__).parent / "fixtures" / "clios_home.html"


def test_extracts_event_links_normalized_to_id_urls():
    urls = clios.find_event_urls(FIXTURE.read_text())
    assert urls == [
        "https://www.eventbrite.com/e/1996519353130",
        "https://www.eventbrite.com/e/1994103149199",  # id-only link, no slug
        "https://www.eventbrite.com/e/1999312777337",
    ]


def test_ignores_organizer_and_site_links():
    urls = clios.find_event_urls(FIXTURE.read_text())
    assert not any("/o/" in u or "membership" in u for u in urls)


def test_scrape_delegates_to_eventbrite(monkeypatch):
    seen = {}
    monkeypatch.setattr(clios, "_fetch_home", lambda: FIXTURE.read_text())
    monkeypatch.setattr(clios.eventbrite, "scrape_event_urls",
                        lambda urls, **kw: seen.setdefault("urls", urls) and [])
    clios.scrape()
    assert len(seen["urls"]) == 3


def test_matches():
    assert clios.matches("https://www.cliosbooks.com/")
    assert not clios.matches("https://www.eventbrite.com/o/other-1")


def test_russianhill_matches_site_and_organizer():
    from scrapers import russianhill
    assert russianhill.matches("https://www.russianhillbookstore.com/events")
    assert russianhill.matches(russianhill.ORGANIZER_URL)
