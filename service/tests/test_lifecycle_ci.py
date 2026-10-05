"""Disappearances and reschedules in the CI merge
(feature-specs/event-lifecycle.md, §3)."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import ci
import event_ids
import lifecycle
from main import save_events
from models import Base, Event, EventAlias
from scrapers.base import RawEvent

DAY0 = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)


def day(n, hour=0):
    return DAY0 + timedelta(days=n, hours=hour)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    with patch("main.get_session", return_value=s):
        yield s
    s.close()


def ev(title, start, url="auto", source=None):
    if url == "auto":
        url = f"https://x.com/{title.replace(' ', '-').lower()}"
    return RawEvent(title=title, start_time=start, location="V", url=url, description=None)


def run(session, scrapes: dict, failed=()):
    """One merge: save each good source's events (in order), then judge.
    `failed` sources are in the run but failed: they save nothing, block."""
    start = datetime.now(timezone.utc)
    for source, events in scrapes.items():
        save_events(events, source=source)
    returned = {s: [e.start_time for e in events] for s, events in scrapes.items() if s not in failed}
    report = lifecycle.judge_disappearances(session, returned, start)
    session.expire_all()
    return report


def rows(session):
    session.expire_all()
    return {e.title: e for e in session.query(Event).all()}


SHOWS = [ev(f"Show {i}", day(i)) for i in range(10)]


# --- misses and unlisting -----------------------------------------------------

def test_two_good_scrapes_without_it_make_a_row_unlisted(session):
    run(session, {"S": SHOWS})
    rest = [e for e in SHOWS if e.title != "Show 3"]
    assert run(session, {"S": rest})["unlisted"] == 0
    r = rows(session)["Show 3"]
    assert (r.status, r.misses) == ("scheduled", {"S": 1})
    assert run(session, {"S": rest})["unlisted"] == 1
    r = rows(session)["Show 3"]
    assert r.status == "unlisted" and r.status_at is not None
    assert all(x.status == "scheduled" for t, x in rows(session).items() if t != "Show 3")


def test_only_rows_inside_the_window_are_judged(session):
    run(session, {"S": SHOWS})
    near = SHOWS[:5]  # the source now only shows days 0–4: says nothing about 5–9
    for _ in range(3):
        run(session, {"S": near})
    assert all(r.status == "scheduled" and not (r.misses or {}).get("S") for r in rows(session).values())


def test_past_rows_are_never_judged(session):
    past = ev("Yesterday", datetime.now(timezone.utc) - timedelta(hours=2))
    session.add(Event(title=past.title, start_time=past.start_time, url=past.url, sources=["S"],
                      created_at=DAY0, status="scheduled"))
    session.commit()
    for _ in range(3):
        run(session, {"S": SHOWS})
    assert rows(session)["Yesterday"].status == "scheduled"


def test_partial_scrape_counts_no_misses_and_is_flagged(session):
    run(session, {"S": SHOWS})
    # 9 of 10 missing (> 20% and > 5): partial. Its window is all 10 (Show 9 is last).
    report = run(session, {"S": [SHOWS[9]]})
    assert report["possibly_partial"] == ["S"]
    assert all(not (r.misses or {}).get("S") for r in rows(session).values())


def test_a_few_missing_is_not_partial_even_above_the_share(session):
    run(session, {"S": SHOWS[:4]})
    # 2 of 4 missing: 50%, but not more than 5 rows, so they count.
    report = run(session, {"S": [SHOWS[0], SHOWS[3]]})
    assert report["possibly_partial"] == []
    assert rows(session)["Show 1"].misses == {"S": 1}


def test_a_source_that_failed_or_was_not_run_blocks_the_decision(session):
    run(session, {"A": SHOWS})
    run(session, {"B": [SHOWS[3]]})  # B lists Show 3 too (merged onto A's row)
    assert rows(session)["Show 3"].sources == ["A", "B"]
    rest = [e for e in SHOWS if e.title != "Show 3"]
    for _ in range(3):  # A drops it, B is not in these runs
        run(session, {"A": rest})
    assert rows(session)["Show 3"].status == "scheduled"
    for _ in range(3):  # B is in the run but fails
        run(session, {"A": rest, "B": []}, failed={"B"})
    assert rows(session)["Show 3"].status == "scheduled"
    # Both judge it missing, twice: gone.
    other = [ev("B Other", day(9))]
    run(session, {"A": rest, "B": other})
    assert rows(session)["Show 3"].status == "scheduled"  # B's first miss
    run(session, {"A": rest, "B": other})
    assert rows(session)["Show 3"].status == "unlisted"


def test_cancelled_rows_are_not_judged(session):
    run(session, {"S": SHOWS})
    session.query(Event).filter_by(title="Show 2").one().status = "cancelled"
    session.commit()
    rest = [e for e in SHOWS if e.title != "Show 2"]
    for _ in range(3):
        run(session, {"S": rest})
    assert rows(session)["Show 2"].status == "cancelled"


def test_a_row_that_comes_back_is_restored(session):
    run(session, {"S": SHOWS})
    rest = [e for e in SHOWS if e.title != "Show 3"]
    run(session, {"S": rest})
    run(session, {"S": rest})
    assert rows(session)["Show 3"].status == "unlisted"
    run(session, {"S": SHOWS})  # a flaky stretch: it's back, no badge
    r = rows(session)["Show 3"]
    assert (r.status, r.misses) == ("scheduled", {"S": 0})


# --- reschedules --------------------------------------------------------------

def test_a_new_time_on_the_same_url_is_a_move(session):
    gig = ev("Gig", day(2))
    run(session, {"S": SHOWS + [gig]})
    old_id = rows(session)["Gig"].id
    moved = ev("Gig", day(4, hour=2), url=gig.url)
    run(session, {"S": SHOWS + [moved]})
    report = run(session, {"S": SHOWS + [moved]})
    assert (report["moved"], report["unlisted"]) == (1, 0)
    gigs = sorted((e for e in session.query(Event).filter_by(title="Gig")), key=lambda e: e.start_time)
    old, new = gigs
    assert (old.id, old.status) == (old_id, "moved")
    assert new.status == "scheduled"
    assert new.changed == {"start_time": day(2).isoformat()} and new.changed_at is not None
    alias = session.get(EventAlias, old_id)
    assert (alias.new_id, alias.reason) == (new.id, "moved")
    assert event_ids.resolve(event_ids.alias_map(session), str(old_id)) == str(new.id)


def test_several_new_times_on_one_url_stay_unlisted(session):
    gig = ev("Gig", day(2))
    run(session, {"S": SHOWS + [gig]})
    new = [ev("Gig", day(4), url=gig.url), ev("Gig", day(5), url=gig.url)]
    run(session, {"S": SHOWS + new})
    report = run(session, {"S": SHOWS + new})
    assert (report["moved"], report["unlisted"]) == (0, 1)
    assert session.query(EventAlias).count() == 0


def test_a_url_shared_by_other_showings_stays_unlisted(session):
    # One show URL, many performances (Berkeley Rep style): one goes, one appears.
    url = "https://rep.org/show"
    perfs = [ev("Play", day(i), url=url) for i in (1, 2, 3)]
    run(session, {"S": SHOWS + perfs})
    now_listed = [perfs[0], perfs[2], ev("Play", day(6), url=url)]
    run(session, {"S": SHOWS + now_listed})
    report = run(session, {"S": SHOWS + now_listed})
    assert (report["moved"], report["unlisted"]) == (0, 1)


def test_a_new_time_that_predates_the_old_rows_last_sighting_is_not_a_move(session):
    # Both times listed side by side for a while (two showings), then the
    # earlier one goes: that's not a reschedule.
    gig = ev("Gig", day(2))
    later = ev("Gig", day(4), url=gig.url)
    run(session, {"S": SHOWS + [gig, later]})
    run(session, {"S": SHOWS + [later]})
    report = run(session, {"S": SHOWS + [later]})
    assert (report["moved"], report["unlisted"]) == (0, 1)


# --- the merge report and the data PR -----------------------------------------

@pytest.fixture
def pipeline_env(tmp_path, monkeypatch):
    sources = tmp_path / "sources.txt"
    sources.write_text("https://first.com\nhttps://second.com\n")
    monkeypatch.setattr("main.SOURCES_FILE", sources)
    monkeypatch.setattr("main.init_db", lambda: None)
    monkeypatch.setattr("main.export_json", lambda path: 0)
    monkeypatch.setattr("main.resolve_places", lambda **kw: None)
    return tmp_path


def _results(tmp, n, scrapes, failed=()):
    d = tmp / f"results-{n}"
    for i, (url, name, events) in enumerate(scrapes):
        (d / f"r{i}").mkdir(parents=True)
        (d / f"r{i}" / "result.json").write_text(json.dumps({
            "url": url, "status": "error" if name in failed else "ok", "source_name": name,
            "error": "boom" if name in failed else None, "events": [e.to_dict() for e in events]}))
    return d


def test_merge_report_and_pr_body_carry_the_lifecycle_numbers(session, pipeline_env):
    first = SHOWS
    changed = [ev("Show 0", day(0))] + [e for e in SHOWS[1:] if e.title != "Show 5"]
    changed[1] = RawEvent(title="Show 1", start_time=day(1), location="New Venue",
                          url=SHOWS[1].url, description=None)
    reports = []
    for n, events in enumerate([first, changed, changed]):
        reports.append(ci.merge_results(_results(pipeline_env, n, [("https://first.com", "First", events)]),
                                        source_filters=["first.com"], classify=False))
    assert reports[1]["lifecycle"]["updated"] == 1
    assert reports[1]["sources"][0]["updated"] == 1
    assert reports[2]["lifecycle"] == {"updated": 0, "unlisted": 1, "moved": 0, "possibly_partial": []}
    body = ci.render_pr_body(reports[2], {"passed": True, "base_count": 10, "count": 9})
    assert "**Changes:** 0 updated · 1 no longer listed · 0 moved" in body
    assert "| Source | Status | Scraped | Saved | Merged | Updated | Skipped |" in body


def test_merge_flags_partial_sources_in_the_table(session, pipeline_env):
    ci.merge_results(_results(pipeline_env, 0, [("https://first.com", "First", SHOWS)]),
                     source_filters=["first.com"], classify=False)
    report = ci.merge_results(_results(pipeline_env, 1, [("https://first.com", "First", SHOWS[9:])]),
                              source_filters=["first.com"], classify=False)
    assert report["lifecycle"]["possibly_partial"] == ["First"]
    assert report["sources"][0]["possibly_partial"] is True
    body = ci.render_pr_body(report, {"passed": True, "base_count": 10, "count": 10})
    assert "⚠️ ok (possibly partial)" in body and "possibly partial (misses not counted): First" in body


def test_merge_does_not_judge_a_failed_source(session, pipeline_env):
    ci.merge_results(_results(pipeline_env, 0, [("https://first.com", "First", SHOWS)]),
                     source_filters=["first.com"], classify=False)
    for n in (1, 2):
        ci.merge_results(_results(pipeline_env, n, [("https://first.com", "First", [])], failed={"First"}),
                         source_filters=["first.com"], classify=False)
    assert all(r.status == "scheduled" and not (r.misses or {}).get("First") for r in rows(session).values())


def test_merge_survives_a_lifecycle_failure(session, pipeline_env, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("bad row")
    monkeypatch.setattr("lifecycle.judge_disappearances", boom)
    report = ci.merge_results(_results(pipeline_env, 0, [("https://first.com", "First", SHOWS)]),
                              source_filters=["first.com"], classify=False)
    assert report["lifecycle"]["error"] == "RuntimeError: bad row"
    assert "skipped (RuntimeError: bad row)" in ci.render_pr_body(
        report, {"passed": True, "base_count": 1, "count": 1})
