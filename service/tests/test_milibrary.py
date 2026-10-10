from datetime import datetime, timezone
from pathlib import Path

from scrapers import milibrary

FIXTURE = Path(__file__).parent / "fixtures" / "milibrary_events.html"


def test_parse_inlined_calendar():
    events = milibrary.parse(FIXTURE.read_text())
    titles = [e.title for e in events]
    # Chess tournaments (tag 972) are dropped; lectures and other events stay.
    assert "24th McClain Memorial Tournament" not in titles
    assert "2026 Fall Tuesday Night Marathon" not in titles
    assert titles == [
        "Movies at Mechanics' Presents: The Awful Truth (1937)",
        "Tech Support Hour",
        "Wine Appreciation Series - Session 5",
        "Chess Lecture from IM Ladia Jirasek",
    ]
    movie = events[0]
    assert movie.start_time == datetime(2026, 10, 3, 1, 0, tzinfo=timezone.utc)  # 6pm PDT
    assert movie.url == "https://www.milibrary.org/events/36276"
    assert movie.location == milibrary.ADDRESS
    assert movie.description.startswith("Join us for fresh popcorn")
    assert movie.image_url.startswith("https://www.milibrary.org/content/events/")
    # Image paths with spaces are URL-quoted.
    assert " " not in events[1].image_url


def test_parse_skips_online_and_private_and_flags_cancelled():
    tpl = ('{"id":"%s","visibility":"%s","title":"%s","start_date":"2026-10-09 18:00:00",'
           '"event_tag_ids":"","cancelled_at":"%s",},')
    html = ("var events = { \"9\":[ "
            + tpl % ("1", "public", "Write If You Dare! (ONLINE)", "")
            + tpl % ("2", "public", "Called Off", "2026-10-01 10:00:00")
            + tpl % ("3", "private", "Members Only", "")
            + tpl % ("4", "public", "Author Talk", "")
            + " ], };\n")
    assert [(e.title, e.status) for e in milibrary.parse(html)] == [
        ("Called Off", "cancelled"), ("Author Talk", None)]


def test_parse_without_calendar_is_empty():
    assert milibrary.parse("<html>maintenance</html>") == []


def test_matches():
    assert milibrary.matches("https://www.milibrary.org/events")
    assert not milibrary.matches("https://sfpl.org/events")
