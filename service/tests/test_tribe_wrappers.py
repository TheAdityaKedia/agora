from datetime import datetime, timezone

from scrapers import atasite, fortmason, jccsf, omca, tribe_events, ybgfestival
from scrapers.base import RawEvent


def _ev(title, day, location=None):
    return RawEvent(title=title, start_time=datetime(2026, 10, day, 19, 0, tzinfo=timezone.utc),
                    location=location, url=f"u{title}{day}", description="d")


def test_matches():
    assert atasite.matches("https://www.atasite.org/")
    assert ybgfestival.matches("https://ybgfestival.org/")
    assert omca.matches("https://museumca.org/")
    assert jccsf.matches("https://www.jccsf.org/")
    assert fortmason.matches("https://fortmason.org/events/")
    assert not omca.matches("https://www.oaklandtheaterproject.org/")


def test_wrappers_pass_their_fallback_address(monkeypatch):
    calls = []
    monkeypatch.setattr(tribe_events, "scrape_events",
                        lambda base, **kw: calls.append((base, kw["fallback_location"])) or [])
    for mod in (atasite, ybgfestival, jccsf):
        mod.scrape()
    assert calls == [(m.SITE_BASE, m.ADDRESS) for m in (atasite, ybgfestival, jccsf)]


def test_omca_expands_bare_campus_venue(monkeypatch):
    raw = [_ev("Friday Nights", 2, "OMCA campus"),
           _ev("Gallery Chat", 3, "James Moore Theater, 1000 Oak St,, Oakland")]
    monkeypatch.setattr(tribe_events, "scrape_events", lambda base, **kw: raw)
    out = {e.title: e.location for e in omca.scrape()}
    assert out["Friday Nights"] == omca.ADDRESS
    assert out["Gallery Chat"] == "James Moore Theater, 1000 Oak St,, Oakland"


def test_fortmason_collapses_daily_exhibition_entries(monkeypatch):
    raw = [_ev("Tied / Untied", d) for d in (5, 3, 4)] + [_ev("Farmers Market", 4)]
    monkeypatch.setattr(tribe_events, "scrape_events", lambda base, **kw: raw)
    out = fortmason.scrape()
    assert sorted(e.title for e in out) == ["Farmers Market", "Tied / Untied"]
    assert next(e for e in out if e.title == "Tied / Untied").start_time.day == 3
