from datetime import datetime, timezone

from scrapers import bay_area, linesballet, factsf, tribe_events, squarespace_events
from scrapers.base import RawEvent


def test_bay_area_filter():
    assert bay_area.is_bay_area("Davies Symphony Hall, 201 Van Ness Ave., San Francisco")
    assert bay_area.is_bay_area("Angelico Concert Hall, Dominican University, San Rafael")
    assert not bay_area.is_bay_area("Harris Theater, 205 E. Randolph, Chicago")
    assert not bay_area.is_bay_area("Wallis Annenberg Theatre, Santa Monica Blvd., Beverly Hills")
    assert not bay_area.is_bay_area(None)


def test_matches():
    assert linesballet.matches("https://linesballet.org/some-event")
    assert not linesballet.matches("https://factsf.org/events")
    assert factsf.matches("https://factsf.org/events")
    assert not factsf.matches("https://linesballet.org")


def _ev(loc):
    return RawEvent(title="Show", start_time=datetime(2026, 10, 1, 2, tzinfo=timezone.utc),
                    location=loc, url="u", description="d")


def test_lines_keeps_only_bay_area(monkeypatch):
    monkeypatch.setattr(tribe_events, "scrape_events",
                        lambda base, **kw: [_ev("Davies Symphony Hall, San Francisco"),
                                             _ev("Harris Theater, Chicago")])
    kept = linesballet.scrape()
    assert [e.location for e in kept] == ["Davies Symphony Hall, San Francisco"]


def test_factsf_delegates(monkeypatch):
    calls = []
    monkeypatch.setattr(squarespace_events, "scrape_collection", lambda url, **kw: calls.append(url) or [])
    factsf.scrape()
    assert calls == [factsf.CALENDAR_URL]
