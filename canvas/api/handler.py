"""Event canvases API — one Lambda behind a Function URL (payload format 2.0).

Routes, validation, CORS, rate limits. Storage lives in store.py, event
snapshots in snapshot.py. Design: feature-specs/event-canvases.md.

Privacy: never log request bodies, names, client ids or IPs. The one log line
per request is method, route, status and latency.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import time
import traceback
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlsplit

import snapshot
import store

MAX_BODY_BYTES = 16 * 1024
LIMITS = {"canvas_name": 80, "note": 1000, "name": 40, "comment": 500,
          "custom_title": 120, "custom_note": 500, "url": 500}
# What one of your own items is; the page shows an icon for it.
CUSTOM_CATEGORIES = {"food", "drinks", "outdoors", "travel", "other"}
# (kind, max requests, window seconds) per salted IP hash.
CREATE_LIMIT = ("create", 10, 3600)
WRITE_LIMIT = ("write", 120, 600)

CLIENT_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
ITEM_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
# Bidi embeddings/overrides/isolates: invisible, and abusable to spoof text.
BIDI_CONTROLS = set("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
CID = r"(?P<cid>[A-Za-z0-9_-]{22})"
IID = r"(?P<iid>[A-Za-z0-9_-]{1,64})"
CMID = r"(?P<cmid>[A-Za-z0-9_-]{1,32})"


class ApiError(Exception):
    def __init__(self, status, message, headers=None):
        super().__init__(message)
        self.status, self.message, self.headers = status, message, headers or {}


# --- validation ---

def _clean(value, field, max_len, *, required=True, multiline=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ApiError(400, f"{field} must be a string")
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\t", " ")
    if not multiline:
        value = value.replace("\n", " ")
    keep = "\n" if multiline else ""
    # Drop control/unassigned/private-use chars and bidi controls, but keep
    # format chars like ZWJ so emoji sequences survive.
    value = "".join(c for c in value
                    if c in keep or (unicodedata.category(c) not in ("Cc", "Cs", "Co", "Cn")
                                     and c not in BIDI_CONTROLS))
    value = value.strip() if multiline else " ".join(value.split())
    if required and not value:
        raise ApiError(400, f"{field} is required")
    if len(value) > max_len:
        raise ApiError(400, f"{field} is longer than {max_len} characters")
    return value


def _date(value, field):
    if value in (None, ""):
        return None
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ApiError(400, f"{field} must be YYYY-MM-DD") from None


def _datetime(value, field):
    if value in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        dt = None
    if dt is None or dt.tzinfo is None:
        raise ApiError(400, f"{field} must be an ISO datetime with a UTC offset")
    return dt.isoformat()


def _url(value, field):
    if value in (None, ""):
        return None
    value = _clean(value, field, LIMITS["url"])
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ApiError(400, f"{field} must be an http(s) link")
    return value


def _actor(body):
    """The display name on a write. Optional: curating a collection for
    yourself never asks for a name; the page asks once others will see it
    (sharing, votes, comments). Empty shows as "Someone"."""
    return _clean(body.get("actor_name"), "actor_name", LIMITS["name"], required=False)


def _check_dates(date_from, date_to):
    if date_from and date_to and date_from > date_to:
        raise ApiError(400, "date_from is after date_to")


# --- helpers ---

def _ip_hash(ip):
    salt = os.environ.get("IP_HASH_SALT") or "agora-canvas"
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:20]


def _rate_limit(req, limit):
    kind, max_n, window = limit
    if store.hit_rate_limit(_ip_hash(req["ip"]), kind, max_n, window):
        raise ApiError(429, "too many requests, slow down",
                       {"Retry-After": str(window - int(time.time()) % window)})


def _require_client(req):
    if not req["client"]:
        raise ApiError(400, "missing or malformed X-Agora-Client header")
    return req["client"]


def _meta_or_404(cid):
    meta = store.get_meta(cid)
    if meta is None:
        raise ApiError(404, "canvas not found")
    return meta


def _item_or_404(cid, iid, *, live=True):
    row = store.get_row(cid, f"ITEM#{iid}")
    if row is None or (live and "removed_at" in row):
        raise ApiError(404, "item not found")
    return row


def _item_title(row):
    return (row.get("event") or {}).get("title") or row.get("title") or ""


def _item_start(row):
    return (row.get("event") or {}).get("start_time") or row.get("start_time")


def _canvas_out(meta, client=""):
    out = {k: meta.get(k) for k in ("id", "name", "note", "date_from", "date_to",
                                    "created_at", "updated_at", "created_by_name")}
    out["plan"] = store.plan_of(meta)
    out["version"] = int(meta["version"])
    # Whether this browser made it or claimed it ("This is mine" on another
    # device). Never returns the ids themselves.
    claimed = bool(client) and client in (meta.get("owner_clients") or set())
    out["yours"] = bool(client) and (meta.get("created_by_client") == client or claimed)
    out["claimed"] = claimed
    return out


def _owners(meta):
    return {meta.get("created_by_client")} | set(meta.get("owner_clients") or ())


def _item_out(row, client=""):
    out = {"id": row["id"], "kind": row["kind"], "added_by_name": row.get("added_by_name"),
           "added_at": row.get("added_at"), "start_time": _item_start(row)}
    if row["kind"] == "event":
        out["event"] = row["event"]
    else:
        out["custom"] = {k: row.get(k) for k in ("title", "url", "start_time", "note", "category")
                         if row.get(k)}
    if "removed_at" in row:
        out["removed_at"] = row["removed_at"]
        out["removed_by_name"] = row.get("removed_by_name")
        out["removed_by_you"] = _by(row.get("removed_by_client"), client)
    return out


def _by(actor_client, client):
    """Whether this browser made a change: True/False, or None for rows from
    before client ids were recorded (the page then falls back to a guess)."""
    if not actor_client:
        return None
    return bool(client) and actor_client == client


def _view(cid, client):
    loaded = store.load_canvas(cid)
    if loaded is None:
        raise ApiError(404, "canvas not found")
    meta, rows, log = loaded
    items, votes, comments = [], defaultdict(list), defaultdict(list)
    # Browsers that have done anything here: the page shows votes, comments
    # and "the plan" only once a collection involves more than one person.
    # The owner's devices (creator + claimed) count as one person.
    owners = _owners(meta)
    people = {"owner"}
    for r in rows:
        kind, rest = r["SK"].split("#", 1)
        if kind == "ITEM":
            items.append(r)
            people.add(r.get("added_by_client"))
        elif kind == "VOTE":
            iid, voter = rest.split("#", 1)
            people.add(voter)
            votes[iid].append({"name": r["name"], "at": r["at"], "mine": voter == client})
        elif kind == "CMT" and "removed_at" not in r:
            people.add(r.get("client_id"))
            iid = rest.split("#", 1)[0]
            comments[iid].append({"id": r["id"], "name": r["name"], "text": r["text"],
                                  "at": r["at"], "mine": r.get("client_id") == client})
    live, removed = [], []
    for r in items:
        out = _item_out(r, client)
        if "removed_at" in r:
            removed.append(out)
            continue
        out["votes"] = sorted(votes[r["id"]], key=lambda v: v["at"])
        out["you_voted"] = any(v["mine"] for v in out["votes"])
        out["comments"] = sorted(comments[r["id"]], key=lambda c: c["at"])
        live.append(out)
    # Dated items by start time, undated (custom) items last, then add order.
    live.sort(key=lambda i: (i["start_time"] is None, i["start_time"] or "", i["added_at"] or ""))
    removed.sort(key=lambda i: i["removed_at"], reverse=True)
    log_out = [{k: e.get(k) for k in ("actor_name", "action", "item_id", "item_title",
                                      "fields", "name", "at") if e.get(k) is not None}
               | ({"mine": _by(e.get("client_id"), client)} if e.get("client_id") else {})
               for e in log]
    people.discard(None)
    people = {"owner" if p in owners else p for p in people}
    canvas = _canvas_out(meta, client)
    live_ids = {i["id"] for i in live}
    canvas["plan"] = [x for x in canvas["plan"] if x in live_ids]
    canvas["people"] = len(people)
    return {"canvas": canvas, "items": live, "removed": removed, "log": log_out}


# --- route handlers: (req, **path params) -> (status, body) ---

def create_canvas(req):
    client = _require_client(req)
    b = req["body"]
    _rate_limit(req, CREATE_LIMIT)
    _rate_limit(req, WRITE_LIMIT)
    fields = {
        "name": _clean(b.get("name"), "name", LIMITS["canvas_name"]),
        "note": _clean(b.get("note"), "note", LIMITS["note"], required=False, multiline=True),
        "date_from": _date(b.get("date_from"), "date_from"),
        "date_to": _date(b.get("date_to"), "date_to"),
    }
    _check_dates(fields["date_from"], fields["date_to"])
    actor = _actor(b)
    meta = store.create_canvas(fields, actor, client)
    return 201, {"canvas": dict(_canvas_out(meta, client), people=1),
                 "items": [], "removed": [], "log": []}


def claim_canvas(req, cid):
    """ "This is mine": count this browser as one of the owner's devices, so
    a collection started on your phone is yours on your PC too (listed under
    Yours, and your additions don't make it look shared). DELETE undoes it.
    Anyone with the link could claim; it changes only how the collection
    is displayed, not what anyone can do."""
    client = _require_client(req)
    _rate_limit(req, WRITE_LIMIT)
    meta = _meta_or_404(cid)
    if req["method"] == "DELETE":
        store.set_owner(cid, client, False)
    elif meta.get("created_by_client") != client:
        try:
            store.set_owner(cid, client, True)
        except store.CapReached as e:
            raise ApiError(409, str(e)) from None
    return 200, _view(cid, client)


def duplicate_canvas(req, cid):
    """A new collection with the same name (or `name`), note, dates and live
    items; no votes, comments or plan. "Share a copy" uses it so a friend
    can add to their own copy without touching yours."""
    client = _require_client(req)
    b = req["body"]
    _rate_limit(req, CREATE_LIMIT)
    _rate_limit(req, WRITE_LIMIT)
    loaded = store.load_canvas(cid)
    if loaded is None:
        raise ApiError(404, "canvas not found")
    meta, rows, _ = loaded
    name = _clean(b.get("name"), "name", LIMITS["canvas_name"], required=False) or meta["name"]
    fields = {"name": name, "note": meta.get("note") or "",
              "date_from": meta.get("date_from"), "date_to": meta.get("date_to")}
    new = store.duplicate_canvas(rows, fields, _actor(b), client, meta["name"])
    return 201, _view(new["id"], client)


def get_canvas(req, cid):
    if_version = req["query"].get("if_version")
    if if_version is not None:
        meta = _meta_or_404(cid)
        if str(int(meta["version"])) == if_version:
            return 200, {"unchanged": True, "version": int(meta["version"])}
    return 200, _view(cid, req["client"])


def patch_canvas(req, cid):
    client = _require_client(req)
    b = req["body"]
    _rate_limit(req, WRITE_LIMIT)
    actor = _actor(b)
    fields = {}
    if "name" in b:
        fields["name"] = _clean(b["name"], "name", LIMITS["canvas_name"])
    if "note" in b:
        fields["note"] = _clean(b["note"], "note", LIMITS["note"], required=False, multiline=True)
    if "date_from" in b:
        fields["date_from"] = _date(b["date_from"], "date_from")
    if "date_to" in b:
        fields["date_to"] = _date(b["date_to"], "date_to")
    meta = _meta_or_404(cid)
    if "date_from" in fields or "date_to" in fields:
        _check_dates(fields.get("date_from", meta.get("date_from")),
                     fields.get("date_to", meta.get("date_to")))
    if not fields:
        raise ApiError(400, "nothing to update")
    try:
        store.update_canvas(cid, fields, actor, client)
    except store.NotFound:
        raise ApiError(404, "canvas not found") from None
    return 200, {"canvas": _canvas_out(_meta_or_404(cid), req["client"])}


def add_item(req, cid):
    client = _require_client(req)
    b = req["body"]
    _rate_limit(req, WRITE_LIMIT)
    actor = _actor(b)
    if ("event_id" in b) == ("custom" in b):
        raise ApiError(400, "send exactly one of event_id or custom")
    if "event_id" in b:
        eid = b["event_id"]
        if not (isinstance(eid, str) and re.fullmatch(r"[A-Za-z0-9-]{1,60}", eid)):
            raise ApiError(400, "event_id is malformed")
        _meta_or_404(cid)
        try:
            ev = snapshot.lookup(eid)
        except snapshot.ManifestUnavailable:
            raise ApiError(503, "event list unavailable, try again shortly") from None
        if ev is None:
            raise ApiError(404, "event not found (it may have passed or been removed)")
        item_id = f"ev_{eid}"
        body = {"kind": "event", "event": snapshot.snapshot_of(ev)}
        title = ev.get("title")
    else:
        sets, _ = _custom_fields(b["custom"])
        title = sets["title"]
        body = {"kind": "custom", **sets}
        item_id = "c_" + secrets.token_urlsafe(6)
    try:
        row, created = store.add_item(cid, item_id, body, actor, client, title)
    except store.NotFound:
        raise ApiError(404, "canvas not found") from None
    except store.CapReached as e:
        raise ApiError(409, str(e)) from None
    except store.Conflict:
        raise ApiError(409, "conflicting edit, try again") from None
    return (201 if created else 200), {"item": _item_out(row), "created": created}


CUSTOM_KEYS = ("title", "url", "start_time", "note", "category")


def _custom_fields(c, *, partial=False):
    """Validate one of your own items' fields -> (values to set, keys to clear).
    New items need a title; an edit (partial) changes only the keys it sends,
    and an empty or null value clears that key (the title can't be cleared)."""
    if not isinstance(c, dict):
        raise ApiError(400, "custom must be an object")
    sets, removes = {}, []
    for k in CUSTOM_KEYS:
        if partial and k not in c:
            continue
        raw = c.get(k)
        if k == "title":
            v = _clean(raw, "custom.title", LIMITS["custom_title"])
        elif k == "url":
            v = _url(raw, "custom.url")
        elif k == "start_time":
            v = _datetime(raw, "custom.start_time")
        elif k == "note":
            v = _clean(raw, "custom.note", LIMITS["custom_note"], required=False, multiline=True)
        else:
            if raw not in (None, "") and raw not in CUSTOM_CATEGORIES:
                raise ApiError(400, "custom.category must be one of " + ", ".join(sorted(CUSTOM_CATEGORIES)))
            v = raw
        if v:
            sets[k] = v
        elif partial:
            removes.append(k)
    if partial and not (sets or removes):
        raise ApiError(400, "nothing to update")
    return sets, removes


def edit_item(req, cid, iid):
    """Change one of your own items (not an Agora event: those are snapshots)."""
    client = _require_client(req)
    b = req["body"]
    _rate_limit(req, WRITE_LIMIT)
    actor = _actor(b)
    sets, removes = _custom_fields(b.get("custom"), partial=True)
    row = _item_or_404(cid, iid)
    if row["kind"] != "custom":
        raise ApiError(400, "only your own items can be edited")
    try:
        store.edit_item(cid, iid, sets, removes, actor, client, sets.get("title") or _item_title(row))
    except store.NotFound as e:
        raise ApiError(404, f"{e} not found") from None
    return 200, {"item": _item_out(_item_or_404(cid, iid))}


def remove_item(req, cid, iid):
    client = _require_client(req)
    _rate_limit(req, WRITE_LIMIT)
    actor = _actor(req["body"])
    row = _item_or_404(cid, iid, live=False)

    def attempt():
        meta = _meta_or_404(cid)
        plan = store.plan_of(meta)
        store.remove_item(cid, iid, actor, client, _item_title(row),
                          plan_after=[x for x in plan if x != iid] if iid in plan else None,
                          expected_version=meta["version"])
    _retry_conflicts(attempt)
    return 200, {"ok": True}


def _retry_conflicts(fn, tries=3):
    """Run a read-modify-write that raises store.Conflict when the canvas
    changed between the read and the write; re-read and retry a few times."""
    for i in range(tries):
        try:
            return fn()
        except store.Conflict:
            if i == tries - 1:
                raise ApiError(409, "conflicting edit, try again") from None
        except store.NotFound as e:
            raise ApiError(404, f"{e} not found") from None


def _plan_insert(plan, iid, rows):
    """Where a newly added step goes: before the first step that starts later,
    so a dated plan stays in time order; undated steps go last. People can
    move steps afterwards."""
    start = _item_start(rows[iid])
    if start:
        for i, pid in enumerate(plan):
            s = _item_start(rows[pid]) if pid in rows else None
            if s and s > start:
                return plan[:i] + [iid] + plan[i:]
    return plan + [iid]


def post_plan(req, cid):
    """{op: add|remove|move, item_id, to?}: change the plan one step at a time,
    so two people editing it at once don't overwrite each other."""
    client = _require_client(req)
    b = req["body"]
    _rate_limit(req, WRITE_LIMIT)
    actor = _actor(b)
    op, iid = b.get("op"), b.get("item_id")
    if op not in ("add", "remove", "move"):
        raise ApiError(400, "op must be add, remove or move")
    if not (isinstance(iid, str) and ITEM_ID_RE.fullmatch(iid)):
        raise ApiError(400, "item_id is malformed")
    to = b.get("to")
    if op == "move" and not (isinstance(to, int) and not isinstance(to, bool)):
        raise ApiError(400, "to must be a position (0 is first)")

    def attempt():
        loaded = store.load_canvas(cid)
        if loaded is None:
            raise ApiError(404, "canvas not found")
        meta, rows, _ = loaded
        items = {r["id"]: r for r in rows if r["SK"].startswith("ITEM#") and "removed_at" not in r}
        plan = [x for x in store.plan_of(meta) if x in items]
        if op == "add":
            if iid not in items:
                raise ApiError(404, "item not found")
            if iid in plan:
                return plan
            if len(plan) >= store.PLAN_CAP:
                raise ApiError(409, f"a plan has at most {store.PLAN_CAP} steps")
            new, action = _plan_insert(plan, iid, items), "added_to_plan"
        elif iid not in plan:
            if op == "remove":
                return plan
            raise ApiError(404, "item is not in the plan")
        elif op == "remove":
            new, action = [x for x in plan if x != iid], "removed_from_plan"
        else:
            new = [x for x in plan if x != iid]
            new.insert(max(0, min(to, len(new))), iid)
            if new == plan:
                return plan
            action = "moved_in_plan"
        store.set_plan(cid, new, meta["version"], actor, client, action, iid, _item_title(items[iid]))
        return new
    return 200, {"plan": _retry_conflicts(attempt)}


def restore_item(req, cid, iid):
    client = _require_client(req)
    _rate_limit(req, WRITE_LIMIT)
    actor = _actor(req["body"])
    row = _item_or_404(cid, iid, live=False)
    try:
        store.restore_item(cid, iid, actor, client, _item_title(row))
    except store.NotFound:
        raise ApiError(404, "canvas not found") from None
    except store.CapReached as e:
        raise ApiError(409, str(e)) from None
    return 200, {"ok": True}


def put_vote(req, cid, iid):
    client = _require_client(req)
    _rate_limit(req, WRITE_LIMIT)
    name = _clean(req["body"].get("name"), "name", LIMITS["name"])
    row = _item_or_404(cid, iid)
    try:
        store.vote(cid, iid, client, name, _item_title(row))
    except store.NotFound as e:
        raise ApiError(404, f"{e} not found") from None
    return 200, {"ok": True}


def delete_vote(req, cid, iid):
    client = _require_client(req)
    _rate_limit(req, WRITE_LIMIT)
    try:
        store.unvote(cid, iid, client)
    except store.NotFound:
        raise ApiError(404, "canvas not found") from None
    return 200, {"ok": True}


def add_comment(req, cid, iid):
    client = _require_client(req)
    _rate_limit(req, WRITE_LIMIT)
    name = _clean(req["body"].get("name"), "name", LIMITS["name"])
    text = _clean(req["body"].get("text"), "text", LIMITS["comment"], multiline=True)
    row = _item_or_404(cid, iid)
    try:
        c = store.add_comment(cid, iid, client, name, text, _item_title(row))
    except store.NotFound as e:
        raise ApiError(404, f"{e} not found") from None
    except store.CapReached as e:
        raise ApiError(409, str(e)) from None
    return 201, {"comment": {"id": c["id"], "name": c["name"], "text": c["text"],
                             "at": c["at"], "mine": True}}


def delete_comment(req, cid, iid, cmid):
    client = _require_client(req)
    _rate_limit(req, WRITE_LIMIT)
    actor = _actor(req["body"])
    row = _item_or_404(cid, iid, live=False)
    try:
        store.delete_comment(cid, iid, cmid, actor, client, _item_title(row))
    except store.NotFound as e:
        raise ApiError(404, f"{e} not found") from None
    return 200, {"ok": True}


ROUTES = [
    (re.compile(r"^/canvases$"), {"POST": create_canvas}),
    (re.compile(rf"^/canvases/{CID}$"), {"GET": get_canvas, "PATCH": patch_canvas}),
    (re.compile(rf"^/canvases/{CID}/duplicate$"), {"POST": duplicate_canvas}),
    (re.compile(rf"^/canvases/{CID}/plan$"), {"POST": post_plan}),
    (re.compile(rf"^/canvases/{CID}/claim$"), {"POST": claim_canvas, "DELETE": claim_canvas}),
    (re.compile(rf"^/canvases/{CID}/items$"), {"POST": add_item}),
    (re.compile(rf"^/canvases/{CID}/items/{IID}$"), {"PATCH": edit_item, "DELETE": remove_item}),
    (re.compile(rf"^/canvases/{CID}/items/{IID}/restore$"), {"POST": restore_item}),
    (re.compile(rf"^/canvases/{CID}/items/{IID}/vote$"), {"PUT": put_vote, "DELETE": delete_vote}),
    (re.compile(rf"^/canvases/{CID}/items/{IID}/comments$"), {"POST": add_comment}),
    (re.compile(rf"^/canvases/{CID}/items/{IID}/comments/{CMID}$"), {"DELETE": delete_comment}),
]


# --- HTTP plumbing ---

def _allowed_origin(origin):
    if not origin:
        return None
    allowed = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    if origin in allowed:
        return origin
    if os.environ.get("ALLOW_LOCALHOST") == "true" and re.fullmatch(
            r"http://(localhost|127\.0\.0\.1)(:\d+)?", origin):
        return origin
    return None


def _cors(origin):
    ok = _allowed_origin(origin)
    if not ok:
        return {}
    return {
        "Access-Control-Allow-Origin": ok,
        "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type,X-Agora-Client",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
    }


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(type(o).__name__)


def _parse_body(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", "replace")
    if len(raw.encode()) > MAX_BODY_BYTES:
        raise ApiError(413, "request body too large")
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        raise ApiError(400, "body must be JSON") from None
    if not isinstance(body, dict):
        raise ApiError(400, "body must be a JSON object")
    return body


def _dispatch(method, path, event, headers):
    for pattern, methods in ROUTES:
        m = pattern.match(path)
        if not m:
            continue
        fn = methods.get(method)
        if fn is None:
            raise ApiError(405, "method not allowed")
        client = headers.get("x-agora-client") or ""
        req = {
            "body": _parse_body(event) if method != "GET" else {},
            "query": event.get("queryStringParameters") or {},
            "client": client if CLIENT_RE.fullmatch(client) else "",
            "method": method,
            "ip": event.get("requestContext", {}).get("http", {}).get("sourceIp", ""),
        }
        return fn.__name__, fn(req, **m.groupdict())
    raise ApiError(404, "not found")


def lambda_handler(event, context):
    started = time.monotonic()
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "GET")
    path = event.get("rawPath") or "/"
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    cors = _cors(headers.get("origin"))
    route, extra = "-", {}
    if method == "OPTIONS":
        status, body = 204, None
    else:
        try:
            route, (status, body) = _dispatch(method, path, event, headers)
        except ApiError as e:
            status, body, extra = e.status, {"error": e.message}, e.headers
        except Exception:
            traceback.print_exc()
            status, body = 500, {"error": "internal error"}
    print(json.dumps({"method": method, "route": route, "status": status,
                      "ms": int((time.monotonic() - started) * 1000)}))
    resp_headers = {"Cache-Control": "no-store", **cors, **extra}
    if body is not None:
        resp_headers["Content-Type"] = "application/json"
    return {
        "statusCode": status,
        "headers": resp_headers,
        "body": json.dumps(body, default=_json_default, ensure_ascii=False) if body is not None else "",
    }
