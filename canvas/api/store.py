"""DynamoDB access for event canvases.

One table, single-table design (fields of every row: canvas/HOW-IT-WORKS.md):

    C#<canvasId>   META                       canvas fields, plan (ordered item ids),
                                              version + counters, owner devices
    C#<canvasId>   ITEM#<itemId>              an event snapshot or a custom item
    C#<canvasId>   VOTE#<itemId>#<clientId>   one 👍 per browser per item
    C#<canvasId>   CMT#<itemId>#<commentId>   a comment
    L#<canvasId>   <ms>#<rand>                activity log (own partition so a
                                              canvas read never drags it all in)
    V#<viewId>     VIEW                       a view-only link -> its canvas
    D#<token>      DEVICE                     a one-time "use on another device"
                                              link (TTL-expired)
    RL#<ipHash>#<kind>#<bucket>  RL           rate-limit counter (TTL-expired)

Every canvas write is one transaction: the change itself + `META.version += 1`
(+ a log row). Polling clients compare versions, so an unchanged canvas costs
one small read.
"""
import os
import secrets
import time
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

LIVE_ITEM_CAP = 100
DEVICE_LINK_TTL_S = 15 * 60
COMMENT_CAP = 1000
LOG_READ_LIMIT = 50
PLAN_CAP = 20

_table = None


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _table


def reset_table_cache():
    """Tests swap TABLE_NAME / mocks between cases."""
    global _table
    _table = None


class NotFound(Exception):
    pass


class CapReached(Exception):
    pass


class Conflict(Exception):
    pass


def new_canvas_id():
    """128 bits → 22 base64url chars. Never starting with "-": such ids read
    as flags on a command line (scripts/admin.py)."""
    while True:
        cid = secrets.token_urlsafe(16)
        if not cid.startswith("-"):
            return cid


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _pk(cid):
    return f"C#{cid}"


def _log_row(cid, actor_name, action, client_id=None, **extra):
    """client_id lets the page say "You" for this browser's own entries; the
    API returns only that yes/no, never the id."""
    row = {
        "PK": f"L#{cid}",
        "SK": f"{int(time.time() * 1000):013d}#{secrets.token_hex(3)}",
        "actor_name": actor_name,
        "action": action,
        "at": now_iso(),
    }
    row.update({k: v for k, v in {"client_id": client_id, **extra}.items() if v is not None})
    return row


def _bump(cid, extra_set="", extra_add="", extra_remove="", condition="", values=None):
    """The META update every write carries: version += 1, updated_at = now."""
    set_parts = ["updated_at = :now"] + ([extra_set] if extra_set else [])
    add_parts = ["version :one"] + ([extra_add] if extra_add else [])
    expr = "SET " + ", ".join(set_parts) + " ADD " + ", ".join(add_parts)
    if extra_remove:
        expr += " REMOVE " + extra_remove
    cond = "attribute_exists(PK)" + (f" AND ({condition})" if condition else "")
    vals = {":now": now_iso(), ":one": 1}
    vals.update(values or {})
    return {"Update": {
        "TableName": table().name,
        "Key": {"PK": _pk(cid), "SK": "META"},
        "UpdateExpression": expr,
        "ConditionExpression": cond,
        "ExpressionAttributeValues": vals,
    }}


def _transact(ops):
    """Run a transaction; on a condition failure raise with the per-op codes."""
    try:
        table().meta.client.transact_write_items(TransactItems=ops)
    except ClientError as e:
        if e.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        reasons = e.response.get("CancellationReasons") or []
        raise _TxFailed([r.get("Code", "None") for r in reasons]) from None


class _TxFailed(Exception):
    def __init__(self, codes):
        super().__init__(codes)
        self.codes = codes

    def failed(self, index):
        return index < len(self.codes) and self.codes[index] == "ConditionalCheckFailed"


def _put(item, condition=None):
    op = {"Put": {"TableName": table().name, "Item": item}}
    if condition:
        op["Put"]["ConditionExpression"] = condition
    return op


def _live_item_check(cid, item_id):
    return {"ConditionCheck": {
        "TableName": table().name,
        "Key": {"PK": _pk(cid), "SK": f"ITEM#{item_id}"},
        "ConditionExpression": "attribute_exists(PK) AND attribute_not_exists(removed_at)",
    }}


# --- reads ---

def get_meta(cid):
    r = table().get_item(Key={"PK": _pk(cid), "SK": "META"}, ConsistentRead=True)
    return r.get("Item")


def get_row(cid, sk):
    r = table().get_item(Key={"PK": _pk(cid), "SK": sk}, ConsistentRead=True)
    return r.get("Item")


def load_canvas(cid):
    """(meta, rows, log) for one canvas; rows excludes META. None if missing."""
    rows, kwargs = [], {"KeyConditionExpression": Key("PK").eq(_pk(cid)), "ConsistentRead": True}
    while True:
        r = table().query(**kwargs)
        rows.extend(r["Items"])
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    meta = next((x for x in rows if x["SK"] == "META"), None)
    if meta is None:
        return None
    log = table().query(
        KeyConditionExpression=Key("PK").eq(f"L#{cid}"),
        ScanIndexForward=False, Limit=LOG_READ_LIMIT,
    )["Items"]
    return meta, [x for x in rows if x["SK"] != "META"], log


# --- writes ---

def create_canvas(fields, actor_name, client_id):
    cid = new_canvas_id()
    now = now_iso()
    meta = {
        "PK": _pk(cid), "SK": "META", "id": cid,
        "name": fields["name"], "note": fields.get("note") or "",
        "version": 1, "item_count": 0, "comment_count": 0,
        "created_at": now, "updated_at": now,
        "created_by_name": actor_name, "created_by_client": client_id,
        # New collections start as plain lists; collections made before this
        # field existed have no `planning` and count as planning on.
        "planning": False,
    }
    for k in ("date_from", "date_to"):
        if fields.get(k):
            meta[k] = fields[k]
    _transact([
        _put(meta, "attribute_not_exists(PK)"),
        _put(_log_row(cid, actor_name, "created", client_id)),
    ])
    return meta


def duplicate_canvas(source_rows, fields, actor_name, client_id, source_name,
                     planning=False, comments=(), copied_from=None):
    """Copy a canvas's live items into a new canvas (no votes, plan or log).
    `comments`: comment rows to carry over (a copy made from a view-only
    page brings the curator's), written as the curator's and owned by no
    browser. Items are written first and META last, so a failure part-way
    leaves only unreachable rows, never a half-copied canvas."""
    cid = new_canvas_id()
    now = now_iso()
    live = [r for r in source_rows
            if r["SK"].startswith("ITEM#") and "removed_at" not in r][:LIVE_ITEM_CAP]
    new_ids, carried = {}, 0
    with table().batch_writer() as batch:
        for r in live:
            item_id = r["id"] if r["kind"] == "event" else "c_" + secrets.token_urlsafe(6)
            new_ids[r["id"]] = item_id
            row = {k: v for k, v in r.items() if k not in ("removed_at", "removed_by_name", "removed_by_client")}
            # Keep who added it (provenance); the copy's maker is its client.
            row.update(PK=_pk(cid), SK=f"ITEM#{item_id}", id=item_id,
                       added_by_client=client_id, added_at=now)
            batch.put_item(Item=row)
        for c in comments:
            item_id = new_ids.get(c["SK"].split("#")[1])
            if item_id is None or carried >= COMMENT_CAP:
                continue
            comment_id = secrets.token_urlsafe(6)
            batch.put_item(Item={"PK": _pk(cid), "SK": f"CMT#{item_id}#{comment_id}", "id": comment_id,
                                 "name": c["name"], "text": c["text"], "at": c["at"],
                                 "from_curator": True})
            carried += 1
    meta = {
        "PK": _pk(cid), "SK": "META", "id": cid,
        "name": fields["name"], "note": fields.get("note") or "",
        "version": 1, "item_count": len(live), "comment_count": carried,
        "created_at": now, "updated_at": now,
        "created_by_name": actor_name, "created_by_client": client_id,
        "planning": bool(planning),
    }
    for k in ("date_from", "date_to"):
        if fields.get(k):
            meta[k] = fields[k]
    if copied_from:
        meta["copied_from"] = copied_from
    action = "copied_from_view" if copied_from else "duplicated"
    _transact([
        _put(meta, "attribute_not_exists(PK)"),
        _put(_log_row(cid, actor_name, action, client_id, name=source_name)),
    ])
    return meta


OWNER_CAP = 20


def set_owner(cid, client_id, on):
    """Add or remove a claimed owner device (META.owner_clients, a string
    set). Bumps the version so open pages re-read who's who."""
    if on:
        bump = _bump(cid, extra_add="owner_clients :c",
                     condition="attribute_not_exists(owner_clients) OR size(owner_clients) < :cap",
                     values={":c": {client_id}, ":cap": OWNER_CAP})
    else:
        bump = _bump(cid, values={":c": {client_id}})
        bump["Update"]["UpdateExpression"] += " DELETE owner_clients :c"
    try:
        _transact([bump])
    except _TxFailed:
        if get_meta(cid) is None:
            raise NotFound("canvas") from None
        raise CapReached(f"a collection can have at most {OWNER_CAP} owner devices") from None


JOINED_CAP = 20


def note_joined(cid, client_id):
    """Record that a browser outside the owner set is writing to the canvas
    (META.joined_clients, a string set), before its write lands. "Solo" then
    survives what leaves no row behind: an un-vote, a restore, a rename or a
    plan change. Bumps the version, so a show or planning-off that read the
    canvas before this fails its version check and re-checks. A few clients
    are enough to tell; past the cap (or if the canvas is gone) it's a no-op."""
    try:
        table().update_item(
            Key={"PK": _pk(cid), "SK": "META"},
            UpdateExpression="ADD joined_clients :c, version :one",
            ConditionExpression="attribute_exists(PK) AND (attribute_not_exists(joined_clients)"
                                " OR size(joined_clients) < :cap)",
            ExpressionAttributeValues={":c": {client_id}, ":one": 1, ":cap": JOINED_CAP})
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def update_canvas(cid, fields, actor_name, client_id=None):
    """`fields` holds only keys being changed; a None value clears that key."""
    names, sets, removes, values, ops = {}, [], [], {}, []
    for i, (k, v) in enumerate(sorted(fields.items())):
        names[f"#f{i}"] = k
        if v is None or (v == "" and k != "note"):
            removes.append(f"#f{i}")
        else:
            sets.append(f"#f{i} = :v{i}")
            values[f":v{i}"] = v
    bump = _bump(cid, extra_set=", ".join(sets), extra_remove=", ".join(removes), values=values)
    bump["Update"]["ExpressionAttributeNames"] = names
    if "name" in fields:
        action = "renamed"
    elif "note" in fields:
        action = "edited_note"
    else:
        action = "set_dates"
    log = _log_row(cid, actor_name, action, client_id, fields=sorted(fields),
                   name=fields.get("name"))
    try:
        _transact([bump, _put(log)] + ops)
    except _TxFailed:
        raise NotFound("canvas") from None


def _transact_checked(cid, ops):
    """A transaction conditioned on what the caller read: NotFound if the
    canvas is gone, else Conflict (it changed meanwhile; re-read and retry)."""
    try:
        _transact(ops)
    except _TxFailed:
        if get_meta(cid) is None:
            raise NotFound("canvas") from None
        raise Conflict() from None


def set_planning(cid, on, actor_name, client_id, expected_version, together=False):
    """Switch planning on or off. Only if nothing changed since the caller
    read the canvas (it checked the rules on that read); turning it on also
    requires that the canvas isn't shown. `together` also records that its
    editable link went out to plan with friends ("Plan it together"), which
    keeps planning on from then on."""
    bump = _bump(cid, extra_set="planning = :p" + (", together = :t" if together else ""),
                 condition="version = :v" + (" AND attribute_not_exists(view_id)" if on else ""),
                 values={":p": bool(on), ":v": expected_version, **({":t": True} if together else {})})
    action = "plan_together" if together else "planning_on" if on else "planning_off"
    _transact_checked(cid, [bump, _put(_log_row(cid, actor_name, action, client_id))])


def get_view_row(view_id):
    r = table().get_item(Key={"PK": f"V#{view_id}", "SK": "VIEW"}, ConsistentRead=True)
    return r.get("Item")


def show_canvas(cid, view_id, show_as, actor_name, client_id, expected_version):
    """Start showing: a new view-only link. Only if nothing changed since the
    caller checked the rules (solo, planning off)."""
    _transact_checked(cid, [
        _bump(cid, extra_set="view_id = :vid, show_as = :sa",
              condition="version = :v AND attribute_not_exists(view_id)",
              values={":vid": view_id, ":sa": show_as, ":v": expected_version}),
        _put({"PK": f"V#{view_id}", "SK": "VIEW", "canvas_id": cid, "created_at": now_iso()},
             "attribute_not_exists(PK)"),
        _put(_log_row(cid, actor_name, "shown", client_id, name=show_as)),
    ])


def rename_show(cid, show_as, actor_name, client_id):
    try:
        _transact([
            _bump(cid, extra_set="show_as = :sa", condition="attribute_exists(view_id)",
                  values={":sa": show_as}),
            _put(_log_row(cid, actor_name, "renamed_show", client_id, name=show_as)),
        ])
    except _TxFailed:
        raise NotFound("view") from None


def stop_showing(cid, view_id, actor_name, client_id):
    """The view-only link stops working (its row stays, so old links can say
    the collection isn't shown anymore). Showing again makes a new link."""
    try:
        _transact([
            _bump(cid, extra_remove="view_id", condition="view_id = :vid", values={":vid": view_id}),
            {"Update": {"TableName": table().name, "Key": {"PK": f"V#{view_id}", "SK": "VIEW"},
                        "UpdateExpression": "SET stopped_at = :now",
                        "ExpressionAttributeValues": {":now": now_iso()}}},
            _put(_log_row(cid, actor_name, "stopped_showing", client_id)),
        ])
    except _TxFailed:
        raise NotFound("view") from None


def make_device_link(cid, client_id):
    """A one-time link that makes another browser an owner device."""
    token = new_canvas_id()
    expires = int(time.time()) + DEVICE_LINK_TTL_S
    table().put_item(Item={"PK": f"D#{token}", "SK": "DEVICE", "canvas_id": cid,
                           "made_by_client": client_id, "ttl": expires})
    return token, expires


def peek_device_link(token):
    """A device link's canvas id without using it up; None if unknown or expired."""
    row = table().get_item(Key={"PK": f"D#{token}", "SK": "DEVICE"}, ConsistentRead=True).get("Item")
    if not row or int(row.get("ttl", 0)) < time.time():
        return None
    return row.get("canvas_id")


def use_device_link(token):
    """Consume a device link: its canvas id, or None if unknown, used or
    expired (DynamoDB deletes expired rows lazily, so check the time too)."""
    try:
        r = table().delete_item(Key={"PK": f"D#{token}", "SK": "DEVICE"},
                                ConditionExpression="attribute_exists(PK)", ReturnValues="ALL_OLD")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise
    row = r.get("Attributes") or {}
    if int(row.get("ttl", 0)) < time.time():
        return None
    return row.get("canvas_id")


def plan_of(meta):
    """The plan: item ids in the order the group will do them. Collections
    from before plans had steps kept one `winner_item_id`."""
    if "plan" in meta:
        return list(meta["plan"])
    return [meta["winner_item_id"]] if meta.get("winner_item_id") else []


def _plan_bump(cid, plan, expected_version, **kw):
    """META update that replaces the plan, only if nobody else wrote since
    `expected_version` (else Conflict: the caller re-reads and retries)."""
    values = {":plan": plan, ":v": expected_version}
    values.update(kw.pop("values", {}))
    bump = _bump(cid, extra_set="#plan = :plan", extra_remove=", ".join(
                     x for x in ("winner_item_id", kw.pop("extra_remove", "")) if x),
                 condition="version = :v", values=values, **kw)
    bump["Update"]["ExpressionAttributeNames"] = {"#plan": "plan"}
    return bump


def set_plan(cid, plan, expected_version, actor_name, client_id, action, item_id, title):
    """Replace the plan (added_to_plan / removed_from_plan / moved_in_plan).
    An item being added must still be live."""
    ops = [_plan_bump(cid, plan, expected_version),
           _put(_log_row(cid, actor_name, action, client_id, item_id=item_id, item_title=title))]
    if action == "added_to_plan":
        ops.append(_live_item_check(cid, item_id))
    try:
        _transact(ops)
    except _TxFailed as e:
        if e.failed(0):
            if get_meta(cid) is None:
                raise NotFound("canvas") from None
            raise Conflict() from None
        if e.failed(2):
            raise NotFound("item") from None
        raise Conflict() from None


def add_item(cid, item_id, body, actor_name, client_id, title):
    """Insert a live item. Returns (row, created). An event already on the
    canvas is returned as-is (and restored if it had been removed)."""
    row = {
        "PK": _pk(cid), "SK": f"ITEM#{item_id}", "id": item_id,
        "added_by_name": actor_name, "added_by_client": client_id, "added_at": now_iso(),
        **body,
    }
    try:
        _transact([
            _bump(cid, extra_add="item_count :one", condition="item_count < :cap",
                  values={":cap": LIVE_ITEM_CAP}),
            _put(_log_row(cid, actor_name, "added", client_id, item_id=item_id, item_title=title)),
            _put(row, "attribute_not_exists(PK)"),
        ])
        return row, True
    except _TxFailed as e:
        if e.failed(2):
            existing = get_row(cid, f"ITEM#{item_id}")
            if existing and "removed_at" in existing:
                restore_item(cid, item_id, actor_name, client_id, title)
                return get_row(cid, f"ITEM#{item_id}"), False
            return existing, False
        if e.failed(0):
            if get_meta(cid) is None:
                raise NotFound("canvas") from None
            raise CapReached(f"a canvas holds at most {LIVE_ITEM_CAP} items") from None
        raise Conflict() from None


def remove_item(cid, item_id, actor_name, client_id, title, plan_after=None, expected_version=None):
    """Soft delete. No-op if already removed. If the item is in the plan, pass
    the plan without it (and the version it was read at): it leaves the plan
    in the same write, or Conflict if the canvas changed meanwhile."""
    item_update = {"Update": {
        "TableName": table().name,
        "Key": {"PK": _pk(cid), "SK": f"ITEM#{item_id}"},
        "UpdateExpression": "SET removed_at = :now, removed_by_name = :who, removed_by_client = :client",
        "ConditionExpression": "attribute_exists(PK) AND attribute_not_exists(removed_at)",
        "ExpressionAttributeValues": {":now": now_iso(), ":who": actor_name, ":client": client_id},
    }}
    if plan_after is None:
        bump = _bump(cid, extra_add="item_count :neg", values={":neg": -1})
    else:
        bump = _plan_bump(cid, plan_after, expected_version,
                          extra_add="item_count :neg", values={":neg": -1})
    try:
        _transact([bump, _put(_log_row(cid, actor_name, "removed", client_id, item_id=item_id,
                                       item_title=title)), item_update])
    except _TxFailed as e:
        if e.failed(0):
            if plan_after is not None and get_meta(cid) is not None:
                raise Conflict() from None
            raise NotFound("canvas") from None
        if e.failed(2):
            return  # already removed (or a concurrent remove won)
        raise Conflict() from None


def edit_item(cid, item_id, sets, removes, actor_name, client_id, title):
    """Change fields of a live custom item (keys from the API's CUSTOM_KEYS)."""
    names = {f"#k{i}": k for i, k in enumerate(list(sets) + removes)}
    by_key = {k: n for n, k in names.items()}
    expr = []
    if sets:
        expr.append("SET " + ", ".join(f"{by_key[k]} = :v{i}" for i, k in enumerate(sets)))
    if removes:
        expr.append("REMOVE " + ", ".join(by_key[k] for k in removes))
    update = {"Update": {
        "TableName": table().name,
        "Key": {"PK": _pk(cid), "SK": f"ITEM#{item_id}"},
        "UpdateExpression": " ".join(expr),
        "ConditionExpression": "attribute_exists(PK) AND attribute_not_exists(removed_at) AND #kind = :custom",
        "ExpressionAttributeNames": {**names, "#kind": "kind"},
        "ExpressionAttributeValues": {":custom": "custom",
                                      **{f":v{i}": v for i, v in enumerate(sets.values())}},
    }}
    try:
        _transact([_bump(cid),
                   _put(_log_row(cid, actor_name, "edited", client_id, item_id=item_id, item_title=title)),
                   update])
    except _TxFailed as e:
        raise NotFound("canvas" if e.failed(0) else "item") from None


def restore_item(cid, item_id, actor_name, client_id, title):
    item_update = {"Update": {
        "TableName": table().name,
        "Key": {"PK": _pk(cid), "SK": f"ITEM#{item_id}"},
        "UpdateExpression": "REMOVE removed_at, removed_by_name, removed_by_client",
        "ConditionExpression": "attribute_exists(removed_at)",
    }}
    try:
        _transact([
            _bump(cid, extra_add="item_count :one", condition="item_count < :cap",
                  values={":cap": LIVE_ITEM_CAP}),
            _put(_log_row(cid, actor_name, "restored", client_id, item_id=item_id, item_title=title)),
            item_update,
        ])
    except _TxFailed as e:
        if e.failed(2):
            return  # not removed: nothing to restore
        if e.failed(0):
            if get_meta(cid) is None:
                raise NotFound("canvas") from None
            raise CapReached(f"a canvas holds at most {LIVE_ITEM_CAP} items") from None
        raise Conflict() from None


def vote(cid, item_id, client_id, name, title):
    row = {"PK": _pk(cid), "SK": f"VOTE#{item_id}#{client_id}", "name": name, "at": now_iso()}
    try:
        _transact([
            _bump(cid),
            _put(_log_row(cid, name, "voted", client_id, item_id=item_id, item_title=title)),
            _put(row),
            _live_item_check(cid, item_id),
        ])
    except _TxFailed as e:
        raise NotFound("canvas" if e.failed(0) else "item") from None


def unvote(cid, item_id, client_id):
    try:
        _transact([
            _bump(cid),
            {"Delete": {"TableName": table().name,
                        "Key": {"PK": _pk(cid), "SK": f"VOTE#{item_id}#{client_id}"}}},
        ])
    except _TxFailed:
        raise NotFound("canvas") from None


def add_comment(cid, item_id, client_id, name, text, title):
    comment_id = secrets.token_urlsafe(6)
    row = {
        "PK": _pk(cid), "SK": f"CMT#{item_id}#{comment_id}", "id": comment_id,
        "name": name, "client_id": client_id, "text": text, "at": now_iso(),
    }
    try:
        _transact([
            _bump(cid, extra_add="comment_count :one", condition="comment_count < :cap",
                  values={":cap": COMMENT_CAP}),
            _put(_log_row(cid, name, "commented", client_id, item_id=item_id, item_title=title)),
            _put(row),
            _live_item_check(cid, item_id),
        ])
    except _TxFailed as e:
        if e.failed(0):
            if get_meta(cid) is None:
                raise NotFound("canvas") from None
            raise CapReached(f"a canvas holds at most {COMMENT_CAP} comments") from None
        raise NotFound("item") from None
    return row


def delete_comment(cid, item_id, comment_id, actor_name, client_id, title):
    update = {"Update": {
        "TableName": table().name,
        "Key": {"PK": _pk(cid), "SK": f"CMT#{item_id}#{comment_id}"},
        "UpdateExpression": "SET removed_at = :now",
        "ConditionExpression": "attribute_exists(PK) AND attribute_not_exists(removed_at)",
        "ExpressionAttributeValues": {":now": now_iso()},
    }}
    try:
        _transact([
            _bump(cid),
            _put(_log_row(cid, actor_name, "deleted_comment", client_id, item_id=item_id, item_title=title)),
            update,
        ])
    except _TxFailed as e:
        if e.failed(0):
            raise NotFound("canvas") from None
        if e.failed(2):
            if get_row(cid, f"CMT#{item_id}#{comment_id}") is None:
                raise NotFound("comment") from None
            return  # already deleted
        raise Conflict() from None


def hit_rate_limit(ip_hash, kind, limit, window_s):
    """Count one request in the current window; True if over the limit."""
    bucket = int(time.time() // window_s)
    r = table().update_item(
        Key={"PK": f"RL#{ip_hash}#{kind}#{bucket}", "SK": "RL"},
        UpdateExpression="ADD #c :one SET #t = if_not_exists(#t, :ttl)",
        ExpressionAttributeNames={"#c": "count", "#t": "ttl"},
        ExpressionAttributeValues={":one": 1, ":ttl": int((bucket + 2) * window_s)},
        ReturnValues="UPDATED_NEW",
    )
    return int(r["Attributes"]["count"]) > limit
