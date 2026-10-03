import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch

import ci
from models import Base, Event
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


# --- merge ------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_venue_resolution(monkeypatch):
    """run()/merge resolve venues against OpenStreetMap and save the committed
    venue files; never from these tests."""
    monkeypatch.setattr("main.resolve_places", lambda **kw: None)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    with patch("main.get_session", return_value=session):
        yield session
    session.close()


@pytest.fixture
def pipeline_env(tmp_path, monkeypatch):
    sources = tmp_path / "sources.txt"
    sources.write_text("https://first.com\nhttps://second.com\nhttps://broken.com\nhttps://gone.com\n")
    monkeypatch.setattr("main.SOURCES_FILE", sources)
    monkeypatch.setattr("main.init_db", lambda: None)
    monkeypatch.setattr("main.export_json", lambda path: 0)
    return tmp_path


def _write_result(results_dir, artifact, url, status="ok", source_name=None, events=(), error=None):
    d = results_dir / artifact
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({
        "url": url, "status": status, "source_name": source_name, "error": error,
        "events": [e.to_dict() for e in events],
    }))


def test_merge_saves_in_sources_order_not_artifact_order(db_session, pipeline_env):
    shared = _raw(title="Shared Show", url=None)  # url None → dedup on title+start_time
    results = pipeline_env / "results"
    # second.com's artifact sorts first on disk; sources.txt order must still win.
    _write_result(results, "result-0", "https://second.com", source_name="Second", events=[shared])
    _write_result(results, "result-1", "https://first.com", source_name="First", events=[shared])

    report = ci.merge_results(results, source_filters=["first.com", "second.com"], classify=False)

    assert db_session.query(Event).one().sources == ["First", "Second"]
    by_url = {r["url"]: r for r in report["sources"]}
    assert by_url["https://first.com"]["saved"] == 1
    assert by_url["https://second.com"]["merged"] == 1
    assert report["failed"] == 0


def test_merge_failed_and_missing_sources_keep_existing_rows(db_session, pipeline_env):
    from main import save_events
    save_events([_raw(title="Old Show", url="https://broken.com/e/1")], source="Broken")
    results = pipeline_env / "results"
    _write_result(results, "result-2", "https://broken.com", status="error",
                  source_name="Broken", error="RuntimeError: 403")
    # gone.com has no artifact at all (job crashed / timed out).

    report = ci.merge_results(results, source_filters=["broken.com", "gone.com"], classify=False)

    assert db_session.query(Event).filter_by(title="Old Show").count() == 1
    by_url = {r["url"]: r for r in report["sources"]}
    assert by_url["https://broken.com"]["status"] == "error"
    assert by_url["https://gone.com"]["status"] == "missing"
    assert report["failed"] == 2


def test_merge_exports_to_given_path(db_session, pipeline_env, monkeypatch):
    seen = {}

    def fake_export(path):
        seen["path"] = path
        return 7

    monkeypatch.setattr("main.export_json", fake_export)
    out = pipeline_env / "out" / "events.json"
    report = ci.merge_results(pipeline_env / "results", classify=False, events_json_path=out)
    assert seen["path"] == out
    assert report["exported"] == 7


def test_merge_scopes_classification_on_filtered_run(db_session, pipeline_env, monkeypatch):
    seen = {}
    monkeypatch.setattr("main.classify_upcoming",
                        lambda source_names=None: seen.setdefault("scope", source_names))
    results = pipeline_env / "results"
    _write_result(results, "result-0", "https://first.com", source_name="First", events=[_raw()])
    ci.merge_results(results, source_filters=["first.com"])
    assert seen["scope"] == {"First"}


def test_merge_survives_classification_failure(db_session, pipeline_env, monkeypatch):
    def boom(source_names=None):
        raise RuntimeError("no AWS creds")

    monkeypatch.setattr("main.classify_upcoming", boom)
    report = ci.merge_results(pipeline_env / "results")  # must not raise
    assert report["exported"] == 0


def test_merge_cli_writes_report(db_session, pipeline_env):
    results = pipeline_env / "results"
    _write_result(results, "result-0", "https://first.com", source_name="First", events=[_raw()])
    report_path = pipeline_env / "report.json"
    ci.cli(["merge", "--dir", str(results), "--report", str(report_path),
            "--sources", "first.com", "--no-classify"])
    report = json.loads(report_path.read_text())
    assert report["sources"][0]["name"] == "First"
    assert report["sources"][0]["saved"] == 1


# --- guard ------------------------------------------------------------------

def _manifest(path, n, generated_at="2026-09-24T00:00:00+00:00"):
    path.write_text(json.dumps({"generated_at": generated_at, "taxonomy": {"version": 1},
                                "events": [{"id": i} for i in range(n)]}))
    return path


def test_guard_passes_at_threshold(tmp_path):
    g = ci.check_manifest(_manifest(tmp_path / "new.json", 70), _manifest(tmp_path / "base.json", 100))
    assert g["passed"] and g["reason"] is None


def test_guard_fails_below_threshold(tmp_path):
    g = ci.check_manifest(_manifest(tmp_path / "new.json", 69), _manifest(tmp_path / "base.json", 100))
    assert not g["passed"]
    assert "100 → 69" in g["reason"]


def test_guard_fails_on_invalid_manifest(tmp_path):
    (tmp_path / "new.json").write_text("{not json")
    g = ci.check_manifest(tmp_path / "new.json", _manifest(tmp_path / "base.json", 10))
    assert not g["passed"]


def test_guard_passes_when_base_has_no_manifest(tmp_path):
    g = ci.check_manifest(_manifest(tmp_path / "new.json", 5), tmp_path / "missing.json")
    assert g["passed"] and g["base_count"] == 0


def test_guard_ignores_generated_at_for_change_detection(tmp_path):
    g = ci.check_manifest(
        _manifest(tmp_path / "new.json", 3, generated_at="2026-09-25T00:00:00+00:00"),
        _manifest(tmp_path / "base.json", 3))
    assert g["changed"] is False


def test_pr_body_flags_failures_and_zero_event_sources():
    row = {"merged": 0, "skipped": 0}
    report = {"exported": 5, "failed": 1, "sources": [
        {**row, "url": "https://a.com", "name": "A", "status": "ok", "events": 5, "saved": 5, "error": None},
        {**row, "url": "https://b.com", "name": "B", "status": "ok", "events": 0, "saved": 0, "error": None},
        {**row, "url": "https://c.com", "name": "C", "status": "error", "events": 0, "saved": 0,
         "error": "RuntimeError: 403"},
    ]}
    guard = {"passed": True, "changed": True, "count": 5, "base_count": 5, "reason": None}
    body = ci.render_pr_body(report, guard)
    assert body.startswith("Automated refresh")
    assert "**Guard:** passed" in body
    assert "ok (0 events)" in body
    assert "RuntimeError: 403" in body


def test_guard_cli_emits_github_outputs(tmp_path, capsys):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"exported": 3, "failed": 0, "sources": []}))
    body = tmp_path / "body.md"
    ci.cli(["guard", "--new", str(_manifest(tmp_path / "new.json", 3)),
            "--base", str(_manifest(tmp_path / "base.json", 3)),
            "--report", str(report), "--body", str(body)])
    assert capsys.readouterr().out.splitlines() == ["passed=true", "changed=false", "count=3"]
    assert body.read_text().startswith("Automated refresh")


# --- alerts -----------------------------------------------------------------

def _row(url, status="ok", events=5, error=None, name=None):
    return {"url": url, "name": name or url, "status": status, "events": events,
            "saved": 0, "merged": 0, "skipped": 0, "error": error}


def test_find_alerts_flags_hard_failures_and_zero_events():
    report = {"sources": [
        _row("https://ok.com"),
        _row("https://err.com", status="error", events=0, error="RuntimeError: boom"),
        _row("https://gone.com", status="missing", events=0),
        _row("https://save.com", status="save_error", error="OperationalError"),
        _row("https://none.com", status="no_scraper", events=0),
        _row("https://empty.com", events=0),
    ]}
    flagged = {r["url"]: r["reason"] for r in ci.find_alerts(report, local_only=[])}
    assert "https://ok.com" not in flagged
    assert flagged["https://err.com"] == "error"
    assert flagged["https://gone.com"] == "missing"
    assert flagged["https://save.com"] == "save_error"
    assert flagged["https://none.com"] == "no_scraper"
    assert flagged["https://empty.com"] == "0 events"


def test_find_alerts_skips_known_local_only_sources():
    report = {"sources": [_row("https://greenapplebooks.com/events", events=0),
                          _row("https://www.cityarts.net/events/", status="error", events=0)]}
    assert ci.find_alerts(report, local_only=["greenapplebooks.com", "cityarts.net"]) == []


def test_load_local_only_ignores_comments(tmp_path):
    f = tmp_path / "local_only_sources.txt"
    f.write_text("# comment\ngreenapplebooks.com\n\n  themarsh.org  # trailing\n")
    assert ci.load_local_only(f) == ["greenapplebooks.com", "themarsh.org"]


def test_render_alert_body_lists_failures_and_mentions_owner():
    alerts = [{**_row("https://err.com", status="error", events=0, error="RuntimeError: 403 `x`",
                      name="Err Venue"), "reason": "error"}]
    body = ci.render_alert_body(alerts, run_url="https://github.com/o/r/actions/runs/1",
                                mention="@owner")
    assert "Err Venue" in body and "RuntimeError: 403 'x'" in body
    assert "https://github.com/o/r/actions/runs/1" in body
    assert "@owner" in body


def test_alert_cli_emits_count_and_writes_body(tmp_path, capsys):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"exported": 1, "failed": 1, "sources": [
        _row("https://a.com"), _row("https://b.com", events=0)]}))
    local_only = tmp_path / "local_only.txt"
    local_only.write_text("")
    body = tmp_path / "alert.md"
    ci.cli(["alert", "--report", str(report), "--local-only", str(local_only),
            "--body", str(body), "--run-url", "https://x/runs/1", "--mention", "@me"])
    assert capsys.readouterr().out.splitlines() == ["count=1"]
    assert "https://b.com" in body.read_text()


# --- places -------------------------------------------------------------------

def _store(tmp_path, pending=None):
    from places.store import Store
    s = Store(tmp_path)
    s.venues["specs-bar"] = {"name": "Specs Bar", "region": "sf", "status": "auto", "precision": "building",
                             "lat": 37.7979, "lng": -122.40652, "address": "12 William Saroyan Pl"}
    for k, p in (pending or {}).items():
        s.locations[k] = {"pending": p}
    return s


def _places_report(**kw):
    base = {"actions": {}, "lookups": 0, "new_venues": [], "new_pending": [], "pending": [],
            "events_with_location": 1000, "unresolved_events": 5}
    return {"sources": [], "failed": 0, "exported": 1000, "places": {**base, **kw}}


def test_places_check_quiet_when_covered(tmp_path):
    check = ci.check_places(_places_report(), _store(tmp_path))
    assert not check["alert"] and check["pending"] == {}
    body = ci.render_places_body(check, _store(tmp_path), "https://run")
    assert "0 place(s) waiting" in body and "99.5% of upcoming events have an area" in body


def test_places_check_alerts_on_low_coverage_and_lists_pending(tmp_path):
    pend = {"lost marbles, 2202 de la vina st": {"reason": "no map result", "events": 4,
                                                 "sources": ["SF Bar Guide"], "first_seen": "2026-10-03"}}
    store = _store(tmp_path, pend)
    check = ci.check_places(_places_report(unresolved_events=40, new_pending=list(pend),
                                           new_venues=["specs-bar"]), store)
    assert check["alert"] and "4.0%" in check["problems"][0]
    body = ci.render_places_body(check, store, "https://run")
    assert "| [lost marbles, 2202 de la vina st](https://www.openstreetmap.org/search?query=lost+marbles" in body
    assert "| 4 | SF Bar Guide | no map result | 2026-10-03 |" in body
    assert "venue_locations.json" in body and "Specs Bar" in body and "mlat=37.7979" in body


def test_places_body_shows_the_ai_suggestion(tmp_path):
    pend = {"the big room": {"reason": "no map result", "events": 2, "sources": ["X"],
                             "suggestion": {"kind": "venue", "name": "Nourse Theater",
                                            "street_address": "275 Hayes St", "city": "San Francisco",
                                            "map": {"label": "Nourse", "osm": "way/9",
                                                    "lat": 37.7781, "lng": -122.4209}}},
            "somewhere": {"reason": "no map result", "events": 1, "sources": ["X"],
                          "suggestion": {"kind": "not_a_place"}}}
    store = _store(tmp_path, pend)
    body = ci.render_places_body(ci.check_places(_places_report(), store), store, "https://run")
    assert "no map result · AI suggests **Nourse Theater, 275 Hayes St, San Francisco** — map found " \
           "[Nourse](https://www.openstreetmap.org/?mlat=37.7781&mlon=-122.4209" in body
    assert "no map result · AI: not a place |" in body


def test_places_check_alerts_on_many_new_pending_and_invalid_files(tmp_path):
    many = [f"s{i}" for i in range(21)]
    assert ci.check_places(_places_report(new_pending=many), _store(tmp_path))["alert"]
    invalid = ci.check_places({"places": {"invalid": ["venues['x']: unknown region"]}}, _store(tmp_path))
    assert invalid["alert"] and "don't validate" in invalid["problems"][0]
    crashed = ci.check_places({"places": {"error": "ConnectionError: boom"}}, _store(tmp_path))
    assert crashed["alert"]
    assert not ci.check_places({"places": None}, _store(tmp_path))["alert"]


def test_places_cli_outputs(tmp_path, capsys):
    pend = {"mystery": {"reason": "no map result", "events": 2, "sources": []}}
    _store(tmp_path, pend).save()
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_places_report(new_pending=["mystery"], pending=["mystery"])))
    ci.cli(["places", "--report", str(report), "--body", str(tmp_path / "b.md"),
            "--run-url", "https://run", "--data-dir", str(tmp_path)])
    assert capsys.readouterr().out.split() == ["pending=1", "new_pending=1", "alert=false"]


def test_pr_body_mentions_venues(tmp_path):
    body = ci.render_pr_body(_places_report(new_venues=["a"], pending=["b", "c"]),
                             {"passed": True, "reason": "", "base_count": 1, "count": 1})
    assert "**Venues:** 1 new · 2 waiting for review · 5 of 1000 events" in body
