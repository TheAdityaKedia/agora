"""Show it, or plan it together (feature-specs/collection-sharing.md)."""
import store
from conftest import EVENT_IDS, Api

E1, E2, E3 = EVENT_IDS
SAM = dict(client="client-sam-0001", ip="5.6.7.8")


def new(api, name="SF jazz this month"):
    r = api("POST", "/canvases", {"name": name, "actor_name": "Mira"})
    assert r["statusCode"] == 201
    return r["json"]["canvas"]["id"]


def view(api, cid):
    r = api("GET", f"/canvases/{cid}")
    assert r["statusCode"] == 200, r["json"]
    return r["json"]


def add(api, cid, eid):
    return api("POST", f"/canvases/{cid}/items", {"event_id": eid, "actor_name": "Mira"})


def show(api, cid, show_as="@sfjazzmira"):
    return api("POST", f"/canvases/{cid}/show", {"show_as": show_as})


# --- planning ---

def test_new_collections_start_as_lists(api):
    cid = new(api)
    c = view(api, cid)["canvas"]
    assert c["planning"] is False and c["solo"] is True
    add(api, cid, E1)
    assert api("PUT", f"/canvases/{cid}/items/ev_{E1}/vote", {"name": "Mira"})["statusCode"] == 409
    assert api("POST", f"/canvases/{cid}/plan", {"op": "add", "item_id": f"ev_{E1}"})["statusCode"] == 409
    # Comments work everywhere: notes to yourself.
    r = api("POST", f"/canvases/{cid}/items/ev_{E1}/comments", {"text": "sit left"})
    assert r["statusCode"] == 201 and r["json"]["comment"]["name"] == ""


def test_legacy_collections_count_as_planning_on(api):
    cid = new(api)
    store.table().update_item(Key={"PK": f"C#{cid}", "SK": "META"}, UpdateExpression="REMOVE planning")
    assert view(api, cid)["canvas"]["planning"] is True


def test_switching_off_hides_and_keeps(api):
    cid = new(api)
    add(api, cid, E1)
    api("PATCH", f"/canvases/{cid}", {"planning": True})
    api("PUT", f"/canvases/{cid}/items/ev_{E1}/vote", {"name": "Mira"})
    api("POST", f"/canvases/{cid}/plan", {"op": "add", "item_id": f"ev_{E1}"})
    assert api("PATCH", f"/canvases/{cid}", {"planning": False})["statusCode"] == 200
    v = view(api, cid)
    assert v["canvas"]["plan"] == [] and v["items"][0]["votes"] == []
    api("PATCH", f"/canvases/{cid}", {"planning": True})
    v = view(api, cid)
    assert v["canvas"]["plan"] == [f"ev_{E1}"] and len(v["items"][0]["votes"]) == 1
    actions = [e["action"] for e in v["log"]]
    assert actions[:1] == ["planning_on"] and "planning_off" in actions


def test_planning_locks_on_once_someone_else_joins(api):
    cid = new(api)
    api("PATCH", f"/canvases/{cid}", {"planning": True})
    sam = Api(**SAM)
    add(sam, cid, E1)
    assert view(api, cid)["canvas"]["solo"] is False
    r = api("PATCH", f"/canvases/{cid}", {"planning": False})
    assert r["statusCode"] == 409 and "planning stays on" in r["json"]["error"]
    # Even after their item is removed: they joined.
    api("DELETE", f"/canvases/{cid}/items/ev_{E1}", {})
    assert api("PATCH", f"/canvases/{cid}", {"planning": False})["statusCode"] == 409


def test_plan_it_together_locks_planning_on_before_anyone_joins(api):
    # "Plan it together" hands out the editable link: from then on it's a
    # planned collection, even while no friend has done anything yet.
    cid = new(api)
    r = api("PATCH", f"/canvases/{cid}", {"together": True, "actor_name": "Mira"})
    assert r["statusCode"] == 200 and r["json"]["canvas"]["planning"] is True
    v = view(api, cid)
    assert v["canvas"]["solo"] is False
    assert [e["action"] for e in v["log"]][:1] == ["plan_together"]
    r = api("PATCH", f"/canvases/{cid}", {"planning": False})
    assert r["statusCode"] == 409 and "planning stays on" in r["json"]["error"]
    r = show(api, cid)
    assert r["statusCode"] == 409 and "planned together" in r["json"]["error"]
    # Saying it again is a no-op; it can't be undone or combined with off.
    version = v["canvas"]["version"]
    assert api("PATCH", f"/canvases/{cid}", {"together": True})["statusCode"] == 200
    assert view(api, cid)["canvas"]["version"] == version
    assert api("PATCH", f"/canvases/{cid}", {"together": False})["statusCode"] == 400
    assert api("PATCH", f"/canvases/{cid}", {"together": True, "planning": False})["statusCode"] == 400


def test_plan_it_together_on_a_solo_plan(api):
    # Planning already on (a solo itinerary): still marks it shared.
    cid = new(api)
    api("PATCH", f"/canvases/{cid}", {"planning": True})
    assert view(api, cid)["canvas"]["solo"] is True
    api("PATCH", f"/canvases/{cid}", {"together": True})
    assert view(api, cid)["canvas"]["solo"] is False


def test_owner_devices_count_as_solo(api):
    cid = new(api)
    pc = Api(client="client-mira-pc01")
    token = api("POST", f"/canvases/{cid}/device-link")["json"]["token"]
    pc("POST", f"/device-links/{token}")
    add(pc, cid, E1)
    assert view(api, cid)["canvas"]["solo"] is True


def test_planning_must_be_a_boolean(api):
    cid = new(api)
    assert api("PATCH", f"/canvases/{cid}", {"planning": "yes"})["statusCode"] == 400


# --- showing ---

def test_show_and_view_page(api):
    cid = new(api)
    add(api, cid, E1)
    add(api, cid, E2)
    api("POST", f"/canvases/{cid}/items/ev_{E1}/comments", {"name": "Mira", "text": "Her trio is unreal"})
    r = show(api, cid)
    assert r["statusCode"] == 200
    vid = r["json"]["view_id"]
    assert vid != cid and r["json"]["canvas"]["shown"] == {"show_as": "@sfjazzmira", "view_id": vid}
    assert show(api, cid)["json"]["view_id"] == vid  # already shown: same link

    stranger = Api(client=None, ip="9.9.9.9")
    page = stranger("GET", f"/views/{vid}")
    assert page["statusCode"] == 200
    body = page["body"]
    assert cid not in body  # never the editable id
    v = page["json"]
    assert v["view"]["name"] == "SF jazz this month" and v["view"]["show_as"] == "@sfjazzmira"
    assert [i["id"] for i in v["items"]] == [f"ev_{E1}", f"ev_{E2}"]
    assert v["items"][0]["comments"] == [{"name": "@sfjazzmira", "text": "Her trio is unreal",
                                         "at": v["items"][0]["comments"][0]["at"]}]
    assert "votes" not in v["items"][0] and "plan" not in v["view"] and "log" not in v
    ver = v["view"]["version"]
    assert stranger("GET", f"/views/{vid}", query={"if_version": str(ver)})["json"]["unchanged"]


def test_view_shows_only_the_owners_comments(api):
    cid = new(api)
    add(api, cid, E1)
    api("POST", f"/canvases/{cid}/items/ev_{E1}/comments", {"name": "Mira", "text": "mine"})
    # A comment from another browser written before showing would make it
    # not solo; simulate one written directly, as from an old shared list.
    store.table().put_item(Item={"PK": f"C#{cid}", "SK": f"CMT#ev_{E1}#x1", "id": "x1",
                                 "name": "Sam", "text": "theirs", "at": "2026-01-01T00:00:00Z",
                                 "client_id": None})
    vid = show(api, cid)["json"]["view_id"]
    texts = [c["text"] for c in api("GET", f"/views/{vid}")["json"]["items"][0]["comments"]]
    assert texts == ["mine"]


def test_only_solo_lists_can_be_shown_by_owners(api):
    cid = new(api)
    sam = Api(**SAM)
    assert show(sam, cid)["statusCode"] == 403  # not an owner device
    api("PATCH", f"/canvases/{cid}", {"planning": True})
    r = show(api, cid)
    assert r["statusCode"] == 409 and "planning off" in r["json"]["error"]
    add(sam, cid, E1)
    api("PATCH", f"/canvases/{cid}", {"planning": False})  # refused: Sam joined
    assert show(api, cid)["statusCode"] == 409


def test_shown_collections_refuse_other_writers(api):
    cid = new(api)
    add(api, cid, E1)
    show(api, cid)
    sam = Api(**SAM)  # holding a leaked editable address
    r = add(sam, cid, E2)
    assert r["statusCode"] == 403 and "@sfjazzmira" in r["json"]["error"]
    assert sam("DELETE", f"/canvases/{cid}/items/ev_{E1}", {})["statusCode"] == 403
    assert sam("POST", f"/canvases/{cid}/items/ev_{E1}/comments", {"name": "S", "text": "hi"})["statusCode"] == 403
    assert sam("PATCH", f"/canvases/{cid}", {"name": "Mine now"})["statusCode"] == 403
    # The owner still edits; turning planning on is refused while shown.
    assert add(api, cid, E2)["statusCode"] == 201
    r = api("PATCH", f"/canvases/{cid}", {"planning": True})
    assert r["statusCode"] == 409 and "Plan with friends" in r["json"]["error"]
    # Others see that it's shown and by whom, not the link.
    assert view(sam, cid)["canvas"]["shown"] == {"show_as": "@sfjazzmira"}


def test_rename_and_stop_showing(api):
    cid = new(api)
    vid = show(api, cid)["json"]["view_id"]
    assert api("PATCH", f"/canvases/{cid}/show", {"show_as": "Mira K"})["statusCode"] == 200
    assert api("GET", f"/views/{vid}")["json"]["view"]["show_as"] == "Mira K"
    assert api("DELETE", f"/canvases/{cid}/show", {})["statusCode"] == 200
    assert api("GET", f"/views/{vid}")["statusCode"] == 410
    assert "shown" not in view(api, cid)["canvas"]
    new_vid = show(api, cid)["json"]["view_id"]
    assert new_vid != vid and api("GET", f"/views/{vid}")["statusCode"] == 410
    assert api("GET", f"/views/{new_vid}")["statusCode"] == 200
    assert api("GET", "/views/" + "a" * 22)["statusCode"] == 404
    assert api("PATCH", f"/canvases/{cid}/show", {"show_as": ""})["statusCode"] == 400


# --- copies from a view-only page ---

def test_plan_with_friends_and_save_a_copy(api):
    cid = new(api)
    add(api, cid, E1)
    add(api, cid, E2)
    api("POST", f"/canvases/{cid}/items/ev_{E1}/comments", {"name": "Mira", "text": "Get the early show"})
    vid = show(api, cid)["json"]["view_id"]
    follower = Api(client="client-follow-01", ip="7.7.7.7")

    r = follower("POST", f"/views/{vid}/copy", {"name": "Saturday with Sam", "planning": True})
    assert r["statusCode"] == 201
    c = r["json"]["canvas"]
    assert c["name"] == "Saturday with Sam" and c["planning"] is True and c["yours"]
    assert c["copied_from"] == {"view_id": vid, "name": "SF jazz this month", "show_as": "@sfjazzmira"}
    items = {i["id"]: i for i in r["json"]["items"]}
    assert set(items) == {f"ev_{E1}", f"ev_{E2}"}
    cm = items[f"ev_{E1}"]["comments"][0]
    assert cm["name"] == "@sfjazzmira" and cm["from_curator"] and not cm["mine"]
    assert c["solo"] and c["people"] == 1  # the carried comments are nobody's
    assert r["json"]["log"][0]["action"] == "copied_from_view"

    r = follower("POST", f"/views/{vid}/copy", {})
    assert r["statusCode"] == 201 and r["json"]["canvas"]["planning"] is False
    assert r["json"]["canvas"]["name"] == "SF jazz this month"
    # The original is untouched.
    assert len(view(api, cid)["items"]) == 2 and view(api, cid)["canvas"]["solo"]

    api("DELETE", f"/canvases/{cid}/show", {})
    assert follower("POST", f"/views/{vid}/copy", {})["statusCode"] == 410
