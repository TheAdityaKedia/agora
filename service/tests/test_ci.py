import json
from datetime import datetime, timedelta, timezone

import pytest

from scrapers.base import RawEvent


def _raw(title="Show", url="https://a.com/e/1", days=1) -> RawEvent:
    start = (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0)
    return RawEvent(title=title, start_time=start, location="Venue, San Francisco, CA",
                    url=url, description="desc", image_url=None)


# --- RawEvent serialization -------------------------------------------------

def test_rawevent_round_trips_through_json():
    raw = _raw()
    d = raw.to_dict()
    assert isinstance(d["start_time"], str)
    assert RawEvent.from_dict(json.loads(json.dumps(d))) == raw


def test_rawevent_round_trip_preserves_none_fields():
    raw = RawEvent(title="T", start_time=datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc),
                   location=None, url=None, description=None)
    assert RawEvent.from_dict(raw.to_dict()) == raw


def test_rawevent_from_dict_rejects_naive_start_time():
    with pytest.raises(ValueError):
        RawEvent.from_dict({"title": "T", "start_time": "2026-10-01T02:00:00",
                            "location": None, "url": None, "description": None,
                            "image_url": None})
