import json
from datetime import datetime, timezone
from pathlib import Path

from scrapers import momence

SESSION = json.loads((Path(__file__).parent / "fixtures" / "momence_session.json").read_text())


def test_session_id_from_short_and_long_links():
    assert momence.session_id("https://momence.com/s/136417618") == "136417618"
    assert momence.session_id(
        "https://momence.com/Berkeley-Alembic/Alembic-Community-Co-Working/136417618?x=1") == "136417618"
    assert momence.session_id("https://momence.com/Berkeley-Alembic") is None
    assert momence.session_id("https://example.com/s/123") is None


def test_event_from_session_detail_uses_physical_address_and_level_description():
    e = momence.event_from_session_detail(SESSION["message"])
    assert e.title == "Alembic Community Co-Working"
    assert e.start_time == datetime(2026, 9, 29, 21, 0, tzinfo=timezone.utc)
    assert e.location == "Berkeley Alembic (Lounge), 2820 Seventh St, Berkeley, CA 94710, USA"
    assert e.url == "https://momence.com/s/136417618"
    assert e.description.startswith("Whether you")  # Momence keeps it in `level`
    assert e.image_url.startswith("https://images.momence.com/")


def test_cancelled_online_or_draft_sessions_are_dropped():
    m = SESSION["message"]
    assert momence.event_from_session_detail(dict(m, isCancelled=True)) is None
    assert momence.event_from_session_detail(dict(m, inPerson=False)) is None
    assert momence.event_from_session_detail(dict(m, isDraft=True)) is None


def test_session_api_url():
    assert momence.session_api_url("136417618") == \
        "https://momence.com/_api/readonly/plugin/sessions/136417618"
