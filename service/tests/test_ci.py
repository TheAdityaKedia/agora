import json
from datetime import datetime, timedelta, timezone

import pytest

import ci
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


# --- plan / scrape ----------------------------------------------------------

def test_plan_matrix_indexes_filtered_sources(tmp_path, monkeypatch):
    f = tmp_path / "sources.txt"
    f.write_text("https://a.com\nhttps://b.com\nhttps://c.com\n")
    monkeypatch.setattr("main.SOURCES_FILE", f)
    assert ci.plan_matrix(["b.com", "c.com"]) == [
        {"index": 0, "url": "https://b.com"},
        {"index": 1, "url": "https://c.com"},
    ]
    assert [m["url"] for m in ci.plan_matrix()] == ["https://a.com", "https://b.com", "https://c.com"]


class _FakeScraper:
    NAME = "Fake Venue"

    def __init__(self, events=None, exc=None):
        self._events = events or []
        self._exc = exc

    def scrape(self, url):
        if self._exc:
            raise self._exc
        return self._events


def test_scrape_to_result_ok(monkeypatch):
    raw = _raw()
    monkeypatch.setattr("main.find_scraper", lambda url: _FakeScraper([raw]))
    r = ci.scrape_to_result("https://a.com")
    assert (r["status"], r["source_name"], r["error"]) == ("ok", "Fake Venue", None)
    assert [RawEvent.from_dict(e) for e in r["events"]] == [raw]


def test_scrape_to_result_records_exception(monkeypatch):
    monkeypatch.setattr("main.find_scraper",
                        lambda url: _FakeScraper(exc=RuntimeError("403 Forbidden")))
    r = ci.scrape_to_result("https://a.com")
    assert r["status"] == "error"
    assert r["error"] == "RuntimeError: 403 Forbidden"
    assert r["source_name"] == "Fake Venue"
    assert r["events"] == []


def test_scrape_to_result_no_scraper(monkeypatch):
    monkeypatch.setattr("main.find_scraper", lambda url: None)
    r = ci.scrape_to_result("https://x.com")
    assert (r["status"], r["source_name"]) == ("no_scraper", None)


def test_scrape_cli_writes_result_and_does_not_raise_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr("main.find_scraper", lambda url: _FakeScraper(exc=RuntimeError("boom")))
    out = tmp_path / "result.json"
    ci.cli(["scrape", "--url", "https://a.com", "--out", str(out)])  # must not raise
    assert json.loads(out.read_text())["status"] == "error"


def test_plan_cli_prints_compact_json(tmp_path, monkeypatch, capsys):
    f = tmp_path / "sources.txt"
    f.write_text("https://a.com\n")
    monkeypatch.setattr("main.SOURCES_FILE", f)
    ci.cli(["plan"])
    assert capsys.readouterr().out.strip() == '[{"index":0,"url":"https://a.com"}]'
