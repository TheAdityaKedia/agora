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


def test_ybgfestival_maps_cross_street_locations():
    cross = _ev("Dance", 7, "Crepe Myrtle Garden, Yerba Buena Gardens, 3rd St. between Mission and Howard Sts., San Francisco")
    numbered = _ev("Kids", 8, "Children’s Garden, Yerba Buena Gardens, 799 Howard St., San Francisco")
    bare = _ev("Fest", 9, None)
    assert ybgfestival.place(cross).location == \
        "Crepe Myrtle Garden, Yerba Buena Gardens, 750 Howard St, San Francisco, CA 94103"
    assert ybgfestival.place(numbered).location == \
        "Children’s Garden, Yerba Buena Gardens, 799 Howard St., San Francisco"  # already mappable
    assert ybgfestival.place(bare).location == ybgfestival.STREET


def test_tribe_falls_back_to_excerpt_when_description_is_missing():
    base = {"title": "Talk", "utc_start_date": "2026-10-10 02:00:00", "url": "u"}
    events = [
        dict(base, description="", excerpt="<p>In this lecture at the JCCSF, marvel at the works.</p>"),
        dict(base, description="<p>B</p>", excerpt="<p>Lace up for an unforgettable fall hike in the Presidio.</p>"),
        dict(base, description="<p>" + "A full description of the event. " * 3 + "</p>", excerpt="<p>Short.</p>"),
    ]
    out = tribe_events.parse_events(events)
    assert out[0].description.startswith("In this lecture")
    assert out[1].description.startswith("Lace up")
    assert out[2].description.startswith("A full description")  # long description wins


def test_fortmason_strips_flattened_tab_bar():
    cases = {
        "@ About Event Details About The Artists Gallery Plan Your Visit 250 Years, Indigenous Futures Presented":
            "250 Years, Indigenous Futures Presented",
        "@ (Donations welcome) About Tour Details Historic Images Plan Your Visit Reserve Space Fort Mason Tour":
            "Fort Mason Tour",
        "@ About Event Features Plan Your Visit Related Events TICKETS Presented By Arion Press":
            "Presented By Arion Press",
        "A normal description with no tab bar.": "A normal description with no tab bar.",
    }
    for raw, want in cases.items():
        assert fortmason.strip_tabs(raw) == want
    assert fortmason.strip_tabs(None) is None
