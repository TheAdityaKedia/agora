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


def _canvas_out(meta):
    out = {k: meta.get(k) for k in ("id", "name", "note", "date_from", "date_to",
                                    "winner_item_id", "created_at", "updated_at",
                                    "created_by_name")}
    out["version"] = int(meta["version"])
    return out


def _item_out(row):
    out = {"id": row["id"], "kind": row["kind"], "added_by_name": row.get("added_by_name"),
           "added_at": row.get("added_at"), "start_time": _item_start(row)}
    if row["kind"] == "event":
        out["event"] = row["event"]
    else:
        out["custom"] = {k: row.get(k) for k in ("title", "url", "start_time", "note")
                         if row.get(k)}
    if "removed_at" in row:
        out["removed_at"] = row["removed_at"]
        out["removed_by_name"] = row.get("removed_by_name")
    return out


def _view(cid, client):
    loaded = store.load_canvas(cid)
    if loaded is None:
        raise ApiError(404, "canvas not found")
    meta, rows, log = loaded
    items, votes, comments = [], defaultdict(list), defaultdict(list)
    for r in rows:
        kind, rest = r["SK"].split("#", 1)
        if kind == "ITEM":
            items.append(r)
        elif kind == "VOTE":
            iid, voter = rest.split("#", 1)
            votes[iid].append({"name": r["name"], "at": r["at"], "mine": voter == client})
        elif kind == "CMT" and "removed_at" not in r:
            iid = rest.split("#", 1)[0]
            comments[iid].append({"id": r["id"], "name": r["name"], "text": r["text"],
                                  "at": r["at"], "mine": r.get("client_id") == client})
    live, removed = [], []
    for r in items:
        out = _item_out(r)
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
               for e in log]
    return {"canvas": _canvas_out(meta), "items": live, "removed": removed, "log": log_out}


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
    actor = _clean(b.get("actor_name"), "actor_name", LIMITS["name"])
    meta = store.create_canvas(fields, actor, client)
    return 201, {"canvas": _canvas_out(meta), "items": [], "removed": [], "log": []}


def get_canvas(req, cid):
    if_version = req["query"].get("if_version")
    if if_version is not None:
        meta = _meta_or_404(cid)
        if str(int(meta["version"])) == if_version:
            return 200, {"unchanged": True, "version": int(meta["version"])}
    return 200, _view(cid, req["client"])


def patch_canvas(req, cid):
    _require_client(req)
    b = req["body"]
    _rate_limit(req, WRITE_LIMIT)
    actor = _clean(b.get("actor_name"), "actor_name", LIMITS["name"])
    fields, winner_title = {}, None
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
    if "winner_item_id" in b:
        wid = b["winner_item_id"]
        if wid is not None and not (isinstance(wid, str) and ITEM_ID_RE.fullmatch(wid)):
            raise ApiError(400, "winner_item_id must be an item id or null")
        if wid:
            winner_title = _item_title(_item_or_404(cid, wid))
        fields["winner_item_id"] = wid
    if not fields:
        raise ApiError(400, "nothing to update")
    try:
        store.update_canvas(cid, fields, actor, winner_title)
    except store.NotFound as e:
        raise ApiError(404, f"{e} not found") from None
    return 200, {"canvas": _canvas_out(_meta_or_404(cid))}


def add_item(req, cid):
    client = _require_client(req)
    b = req["body"]
    _rate_limit(req, WRITE_LIMIT)
    actor = _clean(b.get("actor_name"), "actor_name", LIMITS["name"])
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
        c = b["custom"]
        if not isinstance(c, dict):
            raise ApiError(400, "custom must be an object")
        title = _clean(c.get("title"), "custom.title", LIMITS["custom_title"])
        body = {"kind": "custom", "title": title}
        for k, v in (("url", _url(c.get("url"), "custom.url")),
                     ("start_time", _datetime(c.get("start_time"), "custom.start_time")),
                     ("note", _clean(c.get("note"), "custom.note", LIMITS["custom_note"],
                                     required=False, multiline=True))):
            if v:
                body[k] = v
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


def remove_item(req, cid, iid):
    _require_client(req)
    _rate_limit(req, WRITE_LIMIT)
    actor = _clean(req["body"].get("actor_name"), "actor_name", LIMITS["name"])
    meta = _meta_or_404(cid)
    row = _item_or_404(cid, iid, live=False)
    store.remove_item(cid, iid, actor, _item_title(row),
                      clear_winner=meta.get("winner_item_id") == iid)
    return 200, {"ok": True}


def restore_item(req, cid, iid):
    _require_client(req)
    _rate_limit(req, WRITE_LIMIT)
    actor = _clean(req["body"].get("actor_name"), "actor_name", LIMITS["name"])
    row = _item_or_404(cid, iid, live=False)
    try:
        store.restore_item(cid, iid, actor, _item_title(row))
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
    _require_client(req)
    _rate_limit(req, WRITE_LIMIT)
    actor = _clean(req["body"].get("actor_name"), "actor_name", LIMITS["name"])
    row = _item_or_404(cid, iid, live=False)
    try:
        store.delete_comment(cid, iid, cmid, actor, _item_title(row))
    except store.NotFound as e:
        raise ApiError(404, f"{e} not found") from None
    return 200, {"ok": True}


ROUTES = [
    (re.compile(r"^/canvases$"), {"POST": create_canvas}),
    (re.compile(rf"^/canvases/{CID}$"), {"GET": get_canvas, "PATCH": patch_canvas}),
    (re.compile(rf"^/canvases/{CID}/items$"), {"POST": add_item}),
    (re.compile(rf"^/canvases/{CID}/items/{IID}$"), {"DELETE": remove_item}),
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
