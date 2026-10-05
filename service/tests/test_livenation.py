from datetime import datetime, timezone
from pathlib import Path

from scrapers import cobbs, livenation, punchline, sfmasonic

FIXTURE = Path(__file__).parent / "fixtures" / "livenation_shows.html"
VENUE = "Cobb's Comedy Club, 915 Columbus Ave, San Francisco, CA 94133"


def test_parse_reads_each_jsonld_show():
    events = livenation.parse(FIXTURE.read_text(), venue=VENUE)
    assert [e.title for e in events] == ["Heather McDonald"] * 3
    # Two shows the same night stay separate performances, in UTC.
    assert events[0].start_time == datetime(2026, 10, 3, 2, 0, tzinfo=timezone.utc)
    assert events[1].start_time == datetime(2026, 10, 3, 4, 15, tzinfo=timezone.utc)
    assert all(e.location == VENUE for e in events)
    assert events[0].url.startswith("https://www.ticketmaster.com/")
    assert events[0].image_url.startswith("https://")


def test_parse_skips_cancelled_naive_dates_are_pacific_and_dedupes():
    block = ('<script type="application/ld+json">{"@type":"MusicEvent","name":"%s",'
             '"startDate":"%s","eventStatus":"%s"}</script>')
    html = (block % ("Gone", "2026-10-05T20:00:00", "https://schema.org/EventCancelled")
            + block % ("Late Show", "2026-10-05T21:00:00", "https://schema.org/EventScheduled")
            + block % ("Late Show", "2026-10-05T21:00:00", "https://schema.org/EventScheduled"))
    events = livenation.parse(html, venue=VENUE)
    assert [e.title for e in events] == ["Late Show"]
    assert events[0].start_time == datetime(2026, 10, 6, 4, 0, tzinfo=timezone.utc)


def test_wrappers_match_their_own_sites():
    assert sfmasonic.matches("https://www.sfmasonic.com/shows")
    assert cobbs.matches("https://www.cobbscomedy.com/shows")
    assert punchline.matches("https://www.punchlinecomedyclub.com/shows")
    assert not cobbs.matches("https://www.thefillmore.com/shows")


def test_wrapper_passes_its_venue(monkeypatch):
    seen = {}
    monkeypatch.setattr(livenation, "scrape_shows",
                        lambda url, venue, tag: seen.update(url=url, venue=venue) or [])
    punchline.scrape()
    assert seen == {"url": punchline.EVENTS_URL, "venue": punchline.VENUE}
