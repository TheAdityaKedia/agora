from datetime import datetime, timezone

from scrapers.base import RawEvent
from scrapers import oaklandartmurmur as oam, tribe_events


def _ev(title, day):
    return RawEvent(title=title, start_time=datetime(2026, 10, day, 19, 0, tzinfo=timezone.utc),
                    location="Oakland, CA", url=f"u{title}{day}", description="d")


def test_matches():
    assert oam.matches("https://oaklandartmurmur.org")
    assert not oam.matches("https://birdbeckett.com")


def test_collapse_keeps_earliest_per_title():
    events = [_ev("Night Watch", 5), _ev("Night Watch", 3), _ev("Night Watch", 9),
              _ev("Opening Reception", 4)]
    out = oam.collapse_by_title(events)
    assert len(out) == 2  # two distinct titles
    nw = next(e for e in out if e.title == "Night Watch")
    assert nw.start_time.day == 3  # earliest occurrence kept
    # sorted by start_time
    assert [e.start_time for e in out] == sorted(e.start_time for e in out)


def test_scrape_collapses(monkeypatch):
    raw = [_ev("Show A", 5), _ev("Show A", 6), _ev("Show A", 7), _ev("Reception", 6)]
    monkeypatch.setattr(tribe_events, "scrape_events", lambda base, **kw: raw)
    out = oam.scrape()
    assert sorted(e.title for e in out) == ["Reception", "Show A"]
