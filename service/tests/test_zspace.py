import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrapers.base import RawEvent
from scrapers.zspace import matches, parse_events, NAME

FIXTURES = Path(__file__).parent / "fixtures"
UTC = ZoneInfo("UTC")


@pytest.fixture
def calendar():
    return json.loads((FIXTURES / "zspace_calendar.json").read_text())


@pytest.fixture
def productions():
    return json.loads((FIXTURES / "zspace_productions.json").read_text())


@pytest.fixture
def events(calendar, productions):
    return parse_events(calendar, productions)


def test_matches():
    assert matches("https://www.zspace.org/")
    assert not matches("https://www.sfjazz.org/calendar/")


def test_one_event_per_visible_showtime(events):
    """King Lear has 4 raw showtimes (1 cancelled, 1 hidden) → 2 survive, plus
    Assimilation and One-Night Cabaret → 4 events total."""
    assert len(events) == 4
    assert all(isinstance(e, RawEvent) for e in events)


def test_skips_cancelled_and_hidden(events):
    kl = [e for e in events if e.title == "King Lear"]
    assert len(kl) == 2  # 22:00 cancelled and 27th 19:00 hidden are dropped
    starts = {e.start_time.astimezone(UTC) for e in kl}
    assert datetime(2026, 9, 26, 5, 0, tzinfo=UTC) not in starts  # 22:00 PT cancelled
    assert datetime(2026, 9, 28, 2, 0, tzinfo=UTC) not in starts  # 27th 19:00 PT hidden
    # the two survivors are the 25th 19:00 and 27th 14:00 shows
    assert datetime(2026, 9, 26, 2, 0, tzinfo=UTC) in starts
    assert datetime(2026, 9, 27, 21, 0, tzinfo=UTC) in starts


def test_local_time_converted_to_utc(events):
    kl_first = next(e for e in events if e.title == "King Lear")
    # 2026-09-25 19:00 America/Los_Angeles (PDT, -7) -> 2026-09-26 02:00 UTC
    assert kl_first.start_time.astimezone(UTC) == datetime(2026, 9, 26, 2, 0, tzinfo=UTC)
    assert kl_first.start_time.tzinfo is not None


def test_url_is_show_page_shared_across_performances(events):
    kl = [e for e in events if e.title == "King Lear"]
    assert {e.url for e in kl} == {"https://ci.ovationtix.com/34231/production/1285198"}


def test_location_from_venue(events):
    kl = next(e for e in events if e.title == "King Lear")
    assert kl.location == "Z Space's Steindler Stage"


def test_description_keeps_synopsis_truncates_logistics(events):
    kl = next(e for e in events if e.title == "King Lear")
    assert "An aging king divides his kingdom" in kl.description
    # trailing schedule / logistics blocks are cut at the section header
    assert "Run Time" not in kl.description
    assert "2 hours 30 minutes" not in kl.description
    assert "Content Notice" not in kl.description
    assert "Depictions of violence" not in kl.description


def test_description_presenter_not_duplicated(events):
    """When the synopsis already names the presenter, we don't prepend it again."""
    kl = next(e for e in events if e.title == "King Lear")
    assert kl.description.count("Presented by Oakland Theater Project") == 1


def test_description_prepends_absent_presenter(events):
    """Assimilation's supertitle isn't in its synopsis → prepend it."""
    a = next(e for e in events if e.title == "Assimilation")
    assert a.description.startswith("A Word for Word and Z Space production · ")


def test_image_url_from_logo_file(events):
    kl = next(e for e in events if e.title == "King Lear")
    assert kl.image_url == "https://web.ovationtix.com/trs/api/rest/ClientFile(628302)"


def test_missing_production_falls_back(events):
    """A calendar production absent from the catalog still yields an event with
    the venue name defaulted and no image."""
    cab = next(e for e in events if e.title == "One-Night Cabaret")
    assert cab.location == NAME
    assert cab.image_url is None
    assert cab.url == "https://ci.ovationtix.com/34231/production/1999999"


def test_empty_inputs():
    assert parse_events([], []) == []
    assert parse_events(None, None) == []


# --- venue-homepage image fallback -----------------------------------------

from scrapers.zspace import _parse_homepage_blocks, _match_image, _title_tokens

_HOMEPAGE = """
<html><body>
  <a class="sqs-block-image-link" href="https://www.zspace.org/wfw-assimilation">
    <img data-src="https://images.squarespace-cdn.com/x/assimilation.jpg"/></a>
  <a class="sqs-block-image-link" href="https://www.zspace.org/sketch-on-speed">
    <img src="https://images.squarespace-cdn.com/x/sketch.jpg"/></a>
  <a class="sqs-block-image-link" href="https://www.instagram.com/zspacesf">
    <img src="https://images.squarespace-cdn.com/x/social.jpg"/></a>
</body></html>
"""


def test_parse_homepage_blocks_keeps_shows_drops_social():
    blocks = _parse_homepage_blocks(_HOMEPAGE)
    hrefs = [h for h, _ in blocks]
    assert "https://www.zspace.org/wfw-assimilation" in hrefs
    assert "https://www.zspace.org/sketch-on-speed" in hrefs
    assert not any("instagram" in h for h in hrefs)
    assert blocks[0][1] == "https://images.squarespace-cdn.com/x/assimilation.jpg"


def test_match_image_by_title_tokens():
    entries = [
        (_title_tokens("Assimilation — Z Space"), "assim.jpg"),
        (_title_tokens("Killing My Lobster: Sketch on Speed — Z Space"), "sketch.jpg"),
    ]
    # exact single-word title
    assert _match_image("Assimilation", entries) == "assim.jpg"
    # noisy OvationTix title still matches on token overlap
    assert _match_image(
        "Killing My Lobster & Z Space Present: Sketch on Speed - DECEMBER Edition", entries
    ) == "sketch.jpg"


def test_match_image_returns_none_below_threshold():
    entries = [(_title_tokens("Salt & Spirit — Z Space"), "salt.jpg")]
    assert _match_image("King Lear", entries) is None
