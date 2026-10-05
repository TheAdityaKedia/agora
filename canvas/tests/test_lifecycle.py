"""The current state of events in a collection (feature-specs/
event-lifecycle.md, §6): event-index.json, the `now` overlay, alias-aware
adds, and a plan step placed where an old one was."""
import json

import pytest

import snapshot
from conftest import EVENT_IDS
from test_handler import add_event, view

E1, E2, E3 = EVENT_IDS
OLD = "0ld0ld00-0000-4000-8000-000000000000"  # an id from before a re-key

FIX = json.loads((__import__("conftest").FIXTURE).read_text())["events"]
BASE = {e["id"]: {k: e[k] for k in ("title", "start_time", "location", "url", "image_url", "sources")
                  if e.get(k)} for e in FIX}


@pytest.fixture
def index(tmp_path, monkeypatch):
    """publish(events=…, gone=…, aliases=…) writes event-index.json and makes
    the API read it on the next request."""
    path = tmp_path / "event-index.json"
    monkeypatch.setenv("INDEX_URL", path.as_uri())

    def publish(events=None, gone=None, aliases=None):
        path.write_text(json.dumps({"generated_at": "now", "events": BASE if events is None else events,
                                    "gone": gone or {}, "aliases": aliases or {}}))
        snapshot.reset_cache()
    publish()
    return publish


def _with(eid, **changes):
    return {**BASE, eid: {**BASE[eid], **changes}}


def item(api, cid, item_id=None):
    items = view(api, cid)["items"]
    return items[0] if item_id is None else next(i for i in items if i["id"] == item_id)


# --- reading the index ---------------------------------------------------------

def test_adding_reads_the_index(api, canvas, index):
    r = add_event(api, canvas, E1)
    assert r["statusCode"] == 201
    assert r["json"]["item"]["event"]["title"] == BASE[E1]["title"]


def test_without_an_index_the_manifest_still_adds_and_there_is_no_overlay(api, canvas, monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_URL", (tmp_path / "missing.json").as_uri())
    snapshot.reset_cache()
    assert add_event(api, canvas, E1)["statusCode"] == 201
    assert "now" not in item(api, canvas)


def test_no_overlay_when_nothing_can_be_fetched(api, canvas, index, monkeypatch, tmp_path):
    add_event(api, canvas, E1)
    snapshot.reset_cache()
    monkeypatch.setenv("INDEX_URL", (tmp_path / "missing.json").as_uri())
    monkeypatch.setenv("MANIFEST_URL", (tmp_path / "missing-too.json").as_uri())
    it = item(api, canvas)
    assert "now" not in it and it["event"]["id"] == E1  # the copy, as today


# --- the overlay ---------------------------------------------------------------

def test_an_unchanged_event_is_just_scheduled(api, canvas, index):
    add_event(api, canvas, E1)
    assert item(api, canvas)["now"] == {"status": "scheduled"}


@pytest.mark.parametrize("status", ["cancelled", "postponed"])
def test_cancelled_and_postponed(api, canvas, index, status):
    add_event(api, canvas, E1)
    index(events=_with(E1, status=status))
    assert item(api, canvas)["now"] == {"status": status}


def test_only_what_differs_and_the_current_time_sorts(api, canvas, index):
    add_event(api, canvas, E1)
    add_event(api, canvas, E2)
    later = "2027-01-01T03:00:00+00:00"
    index(events=_with(E1, location="Somewhere New", start_time=later, title="New Title"))
    v = view(api, canvas)
    it = next(i for i in v["items"] if i["event"]["id"] == E1)
    assert it["now"] == {"status": "scheduled", "start_time": later, "location": "Somewhere New",
                         "title": "New Title"}
    assert it["event"]["location"] == BASE[E1].get("location")  # the copy is kept
    assert it["start_time"] == later and v["items"][-1]["id"] == it["id"]


def test_no_longer_listed(api, canvas, index):
    add_event(api, canvas, E1)
    rest = {k: v for k, v in BASE.items() if k != E1}
    index(events=rest, gone={E1: {"status": "unlisted", "at": "x", "title": "t", "start_time": "s"}})
    assert item(api, canvas)["now"] == {"status": "unlisted"}


def test_moved_carries_the_new_showing(api, canvas, index):
    add_event(api, canvas, E1)
    new = "9e9e9e9e-0000-4000-8000-000000000000"
    events = {k: v for k, v in BASE.items() if k != E1}
    events[new] = {**BASE[E1], "start_time": "2027-02-02T04:00:00+00:00",
                   "changed": {"at": "x", "was": {"start_time": BASE[E1]["start_time"]}}}
    index(events=events, gone={E1: {"status": "moved", "at": "x", "title": "t", "start_time": "s",
                                    "moved_to": new}})
    it = item(api, canvas)
    assert it["now"] == {"status": "moved", "moved_to": new, "start_time": "2027-02-02T04:00:00+00:00",
                         **({"location": BASE[E1]["location"]} if BASE[E1].get("location") else {})}
    assert it["start_time"] == BASE[E1]["start_time"]  # still the old showing, until replaced


def test_an_id_that_changed_reports_the_current_one(api, canvas, index):
    index(events={**BASE, OLD: BASE[E1]})  # before the re-key, the event was OLD
    add_event(api, canvas, OLD)
    index(aliases={OLD: E1})              # after: OLD → E1
    it = item(api, canvas)
    assert it["id"] == f"ev_{OLD}" and it["now"] == {"status": "scheduled", "current_id": E1}


def test_an_event_that_left_the_listing_has_no_overlay(api, canvas, index):
    add_event(api, canvas, E1)
    index(events={k: v for k, v in BASE.items() if k != E1})  # passed, or dropped
    assert "now" not in item(api, canvas)


def test_polling_unchanged_is_still_cheap(api, canvas, index):
    add_event(api, canvas, E1)
    version = view(api, canvas)["canvas"]["version"]
    r = api("GET", f"/canvases/{canvas}", query={"if_version": str(version)})
    assert r["json"] == {"unchanged": True, "version": version}


# --- adding through aliases ------------------------------------------------------

def test_adding_an_old_id_copies_the_current_event(api, canvas, index):
    index(aliases={OLD: E1})
    r = add_event(api, canvas, OLD)
    assert r["statusCode"] == 201
    assert r["json"]["item"]["id"] == f"ev_{OLD}"          # the id as added
    assert r["json"]["item"]["event"]["id"] == E1           # the event it is now


def test_an_old_id_of_an_event_already_here_is_not_added_twice(api, canvas, index):
    add_event(api, canvas, E1)
    index(aliases={OLD: E1})
    r = add_event(api, canvas, OLD)
    assert (r["statusCode"], r["json"]["created"], r["json"]["item"]["id"]) == (200, False, f"ev_{E1}")
    assert len(view(api, canvas)["items"]) == 1


def test_the_current_id_of_an_event_added_under_an_old_one_is_not_added_twice(api, canvas, index):
    index(events={**BASE, OLD: BASE[E1]})
    add_event(api, canvas, OLD)
    index(aliases={OLD: E1})
    r = add_event(api, canvas, E1)
    assert (r["statusCode"], r["json"]["created"], r["json"]["item"]["id"]) == (200, False, f"ev_{OLD}")
    assert len(view(api, canvas)["items"]) == 1


def test_adding_again_restores_the_item_under_its_old_id(api, canvas, index):
    index(events={**BASE, OLD: BASE[E1]})
    add_event(api, canvas, OLD)
    api("DELETE", f"/canvases/{canvas}/items/ev_{OLD}", {"actor_name": "Adi"})
    index(aliases={OLD: E1})
    r = add_event(api, canvas, E1)
    assert r["json"]["item"]["id"] == f"ev_{OLD}" and "removed_at" not in r["json"]["item"]
    assert [i["id"] for i in view(api, canvas)["items"]] == [f"ev_{OLD}"]


# --- "Use the new time": a plan step added at a position -------------------------

def test_plan_add_at_a_position(api, canvas, index):
    for e in (E1, E2, E3):
        add_event(api, canvas, e)
    plan = lambda: view(api, canvas)["canvas"]["plan"]  # noqa: E731
    for e in (E1, E3):
        api("POST", f"/canvases/{canvas}/plan", {"op": "add", "item_id": f"ev_{e}"})
    before = plan()
    r = api("POST", f"/canvases/{canvas}/plan", {"op": "add", "item_id": f"ev_{E2}", "to": 0})
    assert r["statusCode"] == 200 and plan() == [f"ev_{E2}"] + before
    bad = api("POST", f"/canvases/{canvas}/plan", {"op": "add", "item_id": f"ev_{E2}", "to": "first"})
    assert bad["statusCode"] == 400
