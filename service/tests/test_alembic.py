import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapers import alembic

FIXTURE = Path(__file__).parent / "fixtures" / "alembic_sessions.json"


@pytest.fixture
def events():
    return alembic.parse_sessions(json.loads(FIXTURE.read_text()))


def test_matches():
    assert alembic.matches("https://www.berkeleyalembic.org/events-2")
    assert not alembic.matches("https://luma.com/frontiertower")


def test_drops_online_copies_and_flags_cancelled(events):
    # fixture: Chalice in person + its (ONLINE) copy, two cancelled, one breathwork
    assert [(e.title, e.status) for e in events] == [
        ("THE CHALICE: Between Science and Spirituality, with Bob Jesse", None),
        ("Soulful Flow with Anne Rene", "cancelled"),
        ("Psychedelic Breathwork with Matt Barkin", None),
        ("Emotional and Relational Surfing: Tools for Developmental Friendship with Brian Basham "
         "and Kedar Shashidhar", "cancelled"),
    ]


def test_event_fields(events):
    chalice = events[0]
    assert chalice.start_time == datetime(2026, 10, 8, 2, 0, tzinfo=timezone.utc)
    assert chalice.url == "https://momence.com/s/142266884"
    assert chalice.location == "The Berkeley Alembic (Sol), 2820 Seventh Street, Berkeley, CA"
    assert chalice.description.startswith("For this month’s Chalice")
    assert chalice.image_url.startswith("https://images.momence.com/")


def test_building_wide_room_uses_plain_address(events):
    # sessions whose "room" is the whole venue don't repeat the venue name
    breathwork = next(e for e in events if e.title.startswith("Psychedelic Breathwork"))
    assert breathwork.location == alembic.ADDRESS
