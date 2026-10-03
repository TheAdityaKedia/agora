import re

import pytest

import snapshot
import store
from conftest import EVENT_IDS, Api

E1, E2, E3 = EVENT_IDS


def view(api, cid):
    r = api("GET", f"/canvases/{cid}")
    assert r["statusCode"] == 200, r["json"]
    return r["json"]


def add_event(api, cid, eid, actor="Adi"):
    return api("POST", f"/canvases/{cid}/items", {"event_id": eid, "actor_name": actor})


# --- create / read / patch ---

def test_create_returns_unguessable_id(api, canvas):
    assert re.fullmatch(r"[A-Za-z0-9_-]{22}", canvas)
    v = view(api, canvas)
    assert v["canvas"]["name"] == "Adi & Sam hangout"
    assert v["canvas"]["date_from"] == "2026-10-10"
    assert v["canvas"]["created_by_name"] == "Adi"
    assert v["items"] == [] and v["log"][0]["action"] == "created"


def test_client_id_never_leaks(api, canvas):
    add_event(api, canvas, E1)
    item_id = view(api, canvas)["items"][0]["id"]
    api("PUT", f"/canvases/{canvas}/items/{item_id}/vote", {"name": "Adi"})
    api("POST", f"/canvases/{canvas}/items/{item_id}/comments", {"name": "Adi", "text": "hi"})
    r = api("GET", f"/canvases/{canvas}")
    assert api.client not in r["body"]


@pytest.mark.parametrize("body,msg", [
    ({"actor_name": "Adi"}, "name is required"),
    ({"name": "x" * 81, "actor_name": "Adi"}, "longer than 80"),
    ({"name": "ok", "actor_name": "Adi", "date_from": "10/10/2026"}, "YYYY-MM-DD"),
    ({"name": "ok", "actor_name": "Adi", "date_from": "2026-10-12", "date_to": "2026-10-10"},
     "after"),
])
def test_create_validation(api, body, msg):
    r = api("POST", "/canvases", body)
    assert r["statusCode"] == 400 and msg in r["json"]["error"]


def test_text_is_cleaned(api):
    r = api("POST", "/canvases", {"name": "  Hang‮ out \x00 ", "actor_name": "A\nB",
                                  "note": "line 1\r\nline 2\x07"})
    c = r["json"]["canvas"]
    assert c["name"] == "Hang out"
    assert c["note"] == "line 1\nline 2"
    assert c["created_by_name"] == "A B"


def test_emoji_sequences_survive(api):
    family = "\U0001F468‍\U0001F469‍\U0001F467"
    r = api("POST", "/canvases", {"name": f"Fam {family}", "actor_name": "Adi"})
    assert r["json"]["canvas"]["name"] == f"Fam {family}"


def test_writes_need_client_header(canvas):
    r = Api(client=None)("POST", f"/canvases/{canvas}/items",
                         {"event_id": E1, "actor_name": "x"})
    assert r["statusCode"] == 400 and "X-Agora-Client" in r["json"]["error"]
    r = Api(client="bad#id")("POST", f"/canvases/{canvas}/items",
                             {"event_id": E1, "actor_name": "x"})
    assert r["statusCode"] == 400


def test_reads_do_not_need_client(canvas):
    assert Api(client=None)("GET", f"/canvases/{canvas}")["statusCode"] == 200


def test_unknown_canvas_404(api):
    assert api("GET", "/canvases/" + "a" * 22)["statusCode"] == 404
    assert add_event(api, "a" * 22, E1)["statusCode"] == 404


def test_routing_errors(api, canvas):
    assert api("GET", "/nope")["statusCode"] == 404
    assert api("GET", "/canvases/short")["statusCode"] == 404
    assert api("DELETE", f"/canvases/{canvas}")["statusCode"] == 405


def test_bad_bodies(api):
    big = {"name": "x", "actor_name": "y", "note": "z" * 20000}
    assert api("POST", "/canvases", big)["statusCode"] == 413
    assert api("POST", "/canvases", [1, 2])["statusCode"] == 400


def test_version_polling(api, canvas):
    v1 = view(api, canvas)["canvas"]["version"]
    r = api("GET", f"/canvases/{canvas}", query={"if_version": str(v1)})
    assert r["json"] == {"unchanged": True, "version": v1}
    add_event(api, canvas, E1)
    r = api("GET", f"/canvases/{canvas}", query={"if_version": str(v1)})
    assert "canvas" in r["json"] and r["json"]["canvas"]["version"] == v1 + 1


def test_patch_fields(api, canvas):
    r = api("PATCH", f"/canvases/{canvas}", {"name": "New name", "note": "dinner first?",
                                             "actor_name": "Sam"})
    assert r["statusCode"] == 200
    assert r["json"]["canvas"]["name"] == "New name"
    assert r["json"]["canvas"]["note"] == "dinner first?"
    r = api("PATCH", f"/canvases/{canvas}", {"date_from": None, "date_to": None,
                                             "actor_name": "Sam"})
    c = r["json"]["canvas"]
    assert c["date_from"] is None and c["date_to"] is None
    assert view(api, canvas)["log"][0]["action"] == "set_dates"


def test_patch_rejects_inverted_dates_against_stored(api, canvas):
    r = api("PATCH", f"/canvases/{canvas}", {"date_from": "2026-10-20", "actor_name": "Sam"})
    assert r["statusCode"] == 400


def test_patch_empty(api, canvas):
    assert api("PATCH", f"/canvases/{canvas}", {"actor_name": "Sam"})["statusCode"] == 400


# --- items ---

def test_add_event_builds_snapshot_from_manifest(api, canvas):
    r = add_event(api, canvas, E1)
    assert r["statusCode"] == 201 and r["json"]["created"]
    ev = r["json"]["item"]["event"]
    assert ev["id"] == E1 and ev["title"] and ev["start_time"]
    assert "description" not in ev  # snapshot carries only display fields


def test_client_cannot_supply_event_data(api, canvas):
    r = api("POST", f"/canvases/{canvas}/items",
            {"event_id": E1, "actor_name": "x", "event": {"title": "FAKE", "image_url": "evil"}})
    assert r["json"]["item"]["event"]["title"] != "FAKE"


def test_unknown_event_404(api, canvas):
    r = add_event(api, canvas, "00000000-0000-0000-0000-000000000000")
    assert r["statusCode"] == 404


def test_add_event_is_idempotent_and_restores(api, canvas):
    add_event(api, canvas, E1)
    r = add_event(api, canvas, E1, actor="Sam")
    assert r["statusCode"] == 200 and not r["json"]["created"]
    assert len(view(api, canvas)["items"]) == 1
    item_id = r["json"]["item"]["id"]
    api("DELETE", f"/canvases/{canvas}/items/{item_id}", {"actor_name": "Sam"})
    assert view(api, canvas)["items"] == []
    r = add_event(api, canvas, E1)
    assert r["statusCode"] == 200 and "removed_at" not in r["json"]["item"]
    assert len(view(api, canvas)["items"]) == 1


def test_custom_item(api, canvas):
    r = api("POST", f"/canvases/{canvas}/items", {"actor_name": "Sam", "custom": {
        "title": "Dinner at Nopa", "url": "https://nopasf.com",
        "start_time": "2026-10-10T18:30:00-07:00", "note": "book ahead"}})
    assert r["statusCode"] == 201
    c = r["json"]["item"]["custom"]
    assert c == {"title": "Dinner at Nopa", "url": "https://nopasf.com",
                 "start_time": "2026-10-10T18:30:00-07:00", "note": "book ahead"}
    assert r["json"]["item"]["id"].startswith("c_")


@pytest.mark.parametrize("custom,msg", [
    ({"title": ""}, "required"),
    ({"title": "x", "url": "javascript:alert(1)"}, "http(s)"),
    ({"title": "x", "start_time": "2026-10-10T18:30"}, "UTC offset"),
])
def test_custom_item_validation(api, canvas, custom, msg):
    r = api("POST", f"/canvases/{canvas}/items", {"actor_name": "Sam", "custom": custom})
    assert r["statusCode"] == 400 and msg in r["json"]["error"]


def test_exactly_one_item_kind(api, canvas):
    r = api("POST", f"/canvases/{canvas}/items",
            {"actor_name": "x", "event_id": E1, "custom": {"title": "y"}})
    assert r["statusCode"] == 400
    assert api("POST", f"/canvases/{canvas}/items", {"actor_name": "x"})["statusCode"] == 400


def test_items_sorted_by_time_undated_last(api, canvas):
    api("POST", f"/canvases/{canvas}/items", {"actor_name": "x", "custom": {"title": "Undated"}})
    for eid in (E1, E2, E3):
        add_event(api, canvas, eid)
    items = view(api, canvas)["items"]
    times = [i["start_time"] for i in items]
    assert times[-1] is None
    assert times[:-1] == sorted(times[:-1])


def test_item_cap(api, canvas, monkeypatch):
    monkeypatch.setattr(store, "LIVE_ITEM_CAP", 2)
    assert add_event(api, canvas, E1)["statusCode"] == 201
    assert add_event(api, canvas, E2)["statusCode"] == 201
    r = add_event(api, canvas, E3)
    assert r["statusCode"] == 409 and "at most 2" in r["json"]["error"]
    # removing frees a slot; restoring past the cap is refused
    first = view(api, canvas)["items"][0]["id"]
    api("DELETE", f"/canvases/{canvas}/items/{first}", {"actor_name": "x"})
    assert add_event(api, canvas, E3)["statusCode"] == 201
    r = api("POST", f"/canvases/{canvas}/items/{first}/restore", {"actor_name": "x"})
    assert r["statusCode"] == 409


def test_soft_delete_and_restore(api, canvas):
    item_id = add_event(api, canvas, E1)["json"]["item"]["id"]
    r = api("DELETE", f"/canvases/{canvas}/items/{item_id}", {"actor_name": "Sam"})
    assert r["statusCode"] == 200
    v = view(api, canvas)
    assert v["items"] == [] and v["removed"][0]["removed_by_name"] == "Sam"
    # removing again is a no-op, not an error
    r = api("DELETE", f"/canvases/{canvas}/items/{item_id}", {"actor_name": "Sam"})
    assert r["statusCode"] == 200
    assert api("POST", f"/canvases/{canvas}/items/{item_id}/restore",
               {"actor_name": "Adi"})["statusCode"] == 200
    v = view(api, canvas)
    assert len(v["items"]) == 1 and v["removed"] == []
    assert [e["action"] for e in v["log"][:3]] == ["restored", "removed", "added"]


def test_removed_items_keep_votes(api, canvas):
    item_id = add_event(api, canvas, E1)["json"]["item"]["id"]
    api("PUT", f"/canvases/{canvas}/items/{item_id}/vote", {"name": "Adi"})
    api("DELETE", f"/canvases/{canvas}/items/{item_id}", {"actor_name": "Sam"})
    api("POST", f"/canvases/{canvas}/items/{item_id}/restore", {"actor_name": "Sam"})
    assert view(api, canvas)["items"][0]["votes"][0]["name"] == "Adi"


# --- winner ---

def test_winner_set_and_cleared_on_remove(api, canvas):
    item_id = add_event(api, canvas, E1)["json"]["item"]["id"]
    r = api("PATCH", f"/canvases/{canvas}", {"winner_item_id": item_id, "actor_name": "Sam"})
    assert r["json"]["canvas"]["winner_item_id"] == item_id
    assert view(api, canvas)["log"][0]["action"] == "picked_winner"
    api("DELETE", f"/canvases/{canvas}/items/{item_id}", {"actor_name": "Sam"})
    assert view(api, canvas)["canvas"]["winner_item_id"] is None


def test_winner_must_be_live_item(api, canvas):
    r = api("PATCH", f"/canvases/{canvas}", {"winner_item_id": "ev_nope", "actor_name": "Sam"})
    assert r["statusCode"] == 404
    r = api("PATCH", f"/canvases/{canvas}", {"winner_item_id": "a#b", "actor_name": "Sam"})
    assert r["statusCode"] == 400


def test_winner_cleared_explicitly(api, canvas):
    item_id = add_event(api, canvas, E1)["json"]["item"]["id"]
    api("PATCH", f"/canvases/{canvas}", {"winner_item_id": item_id, "actor_name": "Sam"})
    r = api("PATCH", f"/canvases/{canvas}", {"winner_item_id": None, "actor_name": "Sam"})
    assert r["json"]["canvas"]["winner_item_id"] is None


# --- votes ---

def test_one_vote_per_client(api, canvas):
    item_id = add_event(api, canvas, E1)["json"]["item"]["id"]
    sam = Api(client="client-sam-0001", ip="5.6.7.8")
    for _ in range(2):
        api("PUT", f"/canvases/{canvas}/items/{item_id}/vote", {"name": "Adi"})
    sam("PUT", f"/canvases/{canvas}/items/{item_id}/vote", {"name": "Sam"})
    item = view(api, canvas)["items"][0]
    assert [v["name"] for v in item["votes"]] == ["Adi", "Sam"]
    assert item["you_voted"] and [v["mine"] for v in item["votes"]] == [True, False]
    assert not view(sam, canvas)["items"][0]["votes"][0]["mine"]


def test_revote_updates_name(api, canvas):
    item_id = add_event(api, canvas, E1)["json"]["item"]["id"]
    api("PUT", f"/canvases/{canvas}/items/{item_id}/vote", {"name": "Adi"})
    api("PUT", f"/canvases/{canvas}/items/{item_id}/vote", {"name": "Aditya"})
    assert [v["name"] for v in view(api, canvas)["items"][0]["votes"]] == ["Aditya"]


def test_unvote(api, canvas):
    item_id = add_event(api, canvas, E1)["json"]["item"]["id"]
    api("PUT", f"/canvases/{canvas}/items/{item_id}/vote", {"name": "Adi"})
    assert api("DELETE", f"/canvases/{canvas}/items/{item_id}/vote")["statusCode"] == 200
    item = view(api, canvas)["items"][0]
    assert item["votes"] == [] and not item["you_voted"]


def test_vote_on_removed_item_404(api, canvas):
    item_id = add_event(api, canvas, E1)["json"]["item"]["id"]
    api("DELETE", f"/canvases/{canvas}/items/{item_id}", {"actor_name": "x"})
    r = api("PUT", f"/canvases/{canvas}/items/{item_id}/vote", {"name": "Adi"})
    assert r["statusCode"] == 404


# --- comments ---

def test_comments(api, canvas):
    item_id = add_event(api, canvas, E1)["json"]["item"]["id"]
    r = api("POST", f"/canvases/{canvas}/items/{item_id}/comments",
            {"name": "Sam", "text": "free after 7"})
    assert r["statusCode"] == 201
    cmid = r["json"]["comment"]["id"]
    c = view(api, canvas)["items"][0]["comments"]
    assert c[0]["text"] == "free after 7" and c[0]["mine"]
    r = api("DELETE", f"/canvases/{canvas}/items/{item_id}/comments/{cmid}", {"actor_name": "Adi"})
    assert r["statusCode"] == 200
    assert view(api, canvas)["items"][0]["comments"] == []
    r = api("DELETE", f"/canvases/{canvas}/items/{item_id}/comments/nope", {"actor_name": "Adi"})
    assert r["statusCode"] == 404


def test_comment_validation(api, canvas):
    item_id = add_event(api, canvas, E1)["json"]["item"]["id"]
    r = api("POST", f"/canvases/{canvas}/items/{item_id}/comments",
            {"name": "Sam", "text": "x" * 501})
    assert r["statusCode"] == 400


def test_comment_cap(api, canvas, monkeypatch):
    monkeypatch.setattr(store, "COMMENT_CAP", 1)
    item_id = add_event(api, canvas, E1)["json"]["item"]["id"]
    path = f"/canvases/{canvas}/items/{item_id}/comments"
    assert api("POST", path, {"name": "a", "text": "1"})["statusCode"] == 201
    assert api("POST", path, {"name": "a", "text": "2"})["statusCode"] == 409


# --- rate limits, CORS, manifest ---

def test_create_rate_limit(api):
    for _ in range(10):
        assert api("POST", "/canvases", {"name": "x", "actor_name": "y"})["statusCode"] == 201
    r = api("POST", "/canvases", {"name": "x", "actor_name": "y"})
    assert r["statusCode"] == 429 and int(r["headers"]["Retry-After"]) > 0
    # a different IP is unaffected
    other = Api(ip="9.9.9.9")
    assert other("POST", "/canvases", {"name": "x", "actor_name": "y"})["statusCode"] == 201


def test_write_rate_limit(api, canvas, monkeypatch):
    import handler
    monkeypatch.setattr(handler, "WRITE_LIMIT", ("write", 3, 600))
    item_id = add_event(api, canvas, E1)["json"]["item"]["id"]
    # The canvas create and the add already used 2 of the 3 writes.
    codes = [api("PUT", f"/canvases/{canvas}/items/{item_id}/vote", {"name": "A"})["statusCode"]
             for _ in range(3)]
    assert codes == [200, 429, 429]


def test_cors(api, canvas, monkeypatch):
    r = api("GET", f"/canvases/{canvas}")
    assert r["headers"]["Access-Control-Allow-Origin"] == "https://theadityakedia.github.io"
    r = api("GET", f"/canvases/{canvas}", origin="https://evil.example")
    assert "Access-Control-Allow-Origin" not in r["headers"]
    r = api("OPTIONS", f"/canvases/{canvas}")
    assert r["statusCode"] == 204 and "X-Agora-Client" in r["headers"]["Access-Control-Allow-Headers"]
    r = api("GET", f"/canvases/{canvas}", origin="http://localhost:8000")
    assert "Access-Control-Allow-Origin" not in r["headers"]
    monkeypatch.setenv("ALLOW_LOCALHOST", "true")
    r = api("GET", f"/canvases/{canvas}", origin="http://localhost:8000")
    assert r["headers"]["Access-Control-Allow-Origin"] == "http://localhost:8000"


def test_manifest_unavailable_503(api, canvas, monkeypatch):
    monkeypatch.setenv("MANIFEST_URL", "file:///does/not/exist.json")
    snapshot.reset_cache()
    assert add_event(api, canvas, E1)["statusCode"] == 503


def test_internal_errors_are_500_without_detail(api, canvas, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret detail")
    monkeypatch.setattr(store, "load_canvas", boom)
    r = api("GET", f"/canvases/{canvas}")
    assert r["statusCode"] == 500 and "secret" not in r["body"]


# --- collections for yourself: optional names, yours/people ---

def test_names_are_optional_for_curating(canvas):
    me = Api(client="client-solo-0001")
    r = me("POST", "/canvases", {"name": "Weekend ideas"})
    assert r["statusCode"] == 201
    cid = r["json"]["canvas"]["id"]
    assert r["json"]["canvas"]["yours"] and r["json"]["canvas"]["people"] == 1
    item = me("POST", f"/canvases/{cid}/items", {"event_id": E1})["json"]["item"]
    assert item["added_by_name"] == ""
    assert me("DELETE", f"/canvases/{cid}/items/{item['id']}")["statusCode"] == 200
    assert me("PATCH", f"/canvases/{cid}", {"note": "x"})["statusCode"] == 200
    # votes and comments are seen by others, so they still need a name
    me("POST", f"/canvases/{cid}/items/{item['id']}/restore")
    assert me("PUT", f"/canvases/{cid}/items/{item['id']}/vote", {})["statusCode"] == 400
    r = me("POST", f"/canvases/{cid}/items/{item['id']}/comments", {"text": "hi"})
    assert r["statusCode"] == 400


def test_yours_and_people(api, canvas):
    sam = Api(client="client-sam-0001", ip="5.6.7.8")
    v = view(api, canvas)["canvas"]
    assert v["yours"] and v["people"] == 1
    assert not view(sam, canvas)["canvas"]["yours"]
    assert not Api(client=None)("GET", f"/canvases/{canvas}")["json"]["canvas"]["yours"]
    item_id = add_event(api, canvas, E1)["json"]["item"]["id"]
    assert view(api, canvas)["canvas"]["people"] == 1  # still just me
    sam("PUT", f"/canvases/{canvas}/items/{item_id}/vote", {"name": "Sam"})
    assert view(api, canvas)["canvas"]["people"] == 2
    assert "created_by_client" not in api("GET", f"/canvases/{canvas}")["body"]


# --- duplicate ---

def test_duplicate_copies_live_items_only(api, canvas):
    e1 = add_event(api, canvas, E1)["json"]["item"]["id"]
    e2 = add_event(api, canvas, E2)["json"]["item"]["id"]
    api("POST", f"/canvases/{canvas}/items", {"actor_name": "Adi", "custom": {"title": "Tacos"}})
    api("DELETE", f"/canvases/{canvas}/items/{e2}", {"actor_name": "Adi"})
    api("PUT", f"/canvases/{canvas}/items/{e1}/vote", {"name": "Adi"})
    api("POST", f"/canvases/{canvas}/items/{e1}/comments", {"name": "Adi", "text": "yes"})
    api("PATCH", f"/canvases/{canvas}", {"winner_item_id": e1, "note": "n", "actor_name": "Adi"})
    sam = Api(client="client-sam-0001", ip="5.6.7.8")
    r = sam("POST", f"/canvases/{canvas}/duplicate", {})
    assert r["statusCode"] == 201
    copy = r["json"]
    assert copy["canvas"]["id"] != canvas
    assert copy["canvas"]["name"] == "Adi & Sam hangout"
    assert copy["canvas"]["note"] == "n" and copy["canvas"]["date_from"] == "2026-10-10"
    assert copy["canvas"]["yours"] and copy["canvas"]["people"] == 1
    assert copy["canvas"]["winner_item_id"] is None
    titles = sorted(i.get("event", {}).get("title") or i["custom"]["title"] for i in copy["items"])
    assert len(titles) == 2 and "Tacos" in titles
    assert all(i["votes"] == [] and i["comments"] == [] for i in copy["items"])
    assert copy["removed"] == []
    assert copy["log"][0]["action"] == "duplicated"
    # The original is untouched, and the copy evolves on its own.
    cc = copy["canvas"]["id"]
    sam("POST", f"/canvases/{cc}/items", {"event_id": E3})
    assert len(view(api, canvas)["items"]) == 2
    assert len(view(sam, cc)["items"]) == 3


def test_duplicate_custom_items_get_new_ids(api, canvas):
    cid = api("POST", f"/canvases/{canvas}/items", {"custom": {"title": "Tacos"}})["json"]["item"]["id"]
    copy = api("POST", f"/canvases/{canvas}/duplicate", {"name": "Mine"})["json"]
    assert copy["canvas"]["name"] == "Mine"
    assert copy["items"][0]["id"] != cid and copy["items"][0]["id"].startswith("c_")


def test_duplicate_unknown_404(api):
    assert api("POST", "/canvases/" + "a" * 22 + "/duplicate", {})["statusCode"] == 404


# --- "This is mine": claiming from another device ---

def test_claim_makes_it_yours_and_one_person(api, canvas):
    pc = Api(client="client-adi-pc-01", ip="1.2.3.4")  # Adi's other device
    v = view(pc, canvas)["canvas"]
    assert not v["yours"] and not v["claimed"]
    r = pc("POST", f"/canvases/{canvas}/claim")
    assert r["statusCode"] == 200
    assert r["json"]["canvas"]["yours"] and r["json"]["canvas"]["claimed"]
    # Adding from both devices is still one person: the list stays personal.
    add_event(api, canvas, E1)
    add_event(pc, canvas, E2)
    assert view(api, canvas)["canvas"]["people"] == 1
    assert view(pc, canvas)["canvas"]["people"] == 1
    # The creator's own view is unchanged ("created", not "claimed").
    assert view(api, canvas)["canvas"]["yours"] and not view(api, canvas)["canvas"]["claimed"]
    # Someone else still counts as a second person.
    sam = Api(client="client-sam-0001", ip="5.6.7.8")
    sam("PUT", f"/canvases/{canvas}/items/ev_{E1}/vote", {"name": "Sam"})
    assert view(api, canvas)["canvas"]["people"] == 2


def test_unclaim(api, canvas):
    pc = Api(client="client-adi-pc-01")
    pc("POST", f"/canvases/{canvas}/claim")
    add_event(pc, canvas, E1)
    r = pc("DELETE", f"/canvases/{canvas}/claim")
    assert r["statusCode"] == 200 and not r["json"]["canvas"]["yours"]
    assert view(api, canvas)["canvas"]["people"] == 2  # pc is a separate person again


def test_claim_is_idempotent_and_creator_noop(api, canvas):
    v1 = view(api, canvas)["canvas"]["version"]
    r = api("POST", f"/canvases/{canvas}/claim")  # the creator: nothing to do
    assert r["statusCode"] == 200 and r["json"]["canvas"]["version"] == v1
    pc = Api(client="client-adi-pc-01")
    for _ in range(2):
        assert pc("POST", f"/canvases/{canvas}/claim")["json"]["canvas"]["claimed"]


def test_claim_cap_and_404(api, canvas, monkeypatch):
    import store
    monkeypatch.setattr(store, "OWNER_CAP", 1)
    assert Api(client="client-dev-00001")("POST", f"/canvases/{canvas}/claim")["statusCode"] == 200
    assert Api(client="client-dev-00002")("POST", f"/canvases/{canvas}/claim")["statusCode"] == 409
    assert api("POST", "/canvases/" + "a" * 22 + "/claim")["statusCode"] == 404


def test_claim_needs_client(canvas):
    assert Api(client=None)("POST", f"/canvases/{canvas}/claim")["statusCode"] == 400


def test_duplicate_does_not_copy_owners(api, canvas):
    pc = Api(client="client-adi-pc-01")
    pc("POST", f"/canvases/{canvas}/claim")
    copy = api("POST", f"/canvases/{canvas}/duplicate", {})["json"]["canvas"]["id"]
    assert not view(pc, copy)["canvas"]["yours"]
