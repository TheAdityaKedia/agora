"""DynamoDB access for event canvases.

One table, single-table design (see feature-specs/event-canvases.md):

    C#<canvasId>   META                       canvas fields + version + counters
    C#<canvasId>   ITEM#<itemId>              an event snapshot or a custom item
    C#<canvasId>   VOTE#<itemId>#<clientId>   one 👍 per browser per item
    C#<canvasId>   CMT#<itemId>#<commentId>   a comment
    L#<canvasId>   <ms>#<rand>                activity log (own partition so a
                                              canvas read never drags it all in)
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
COMMENT_CAP = 1000
LOG_READ_LIMIT = 50

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
    return secrets.token_urlsafe(16)  # 128 bits → 22 base64url chars


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _pk(cid):
    return f"C#{cid}"


def _log_row(cid, actor_name, action, **extra):
    row = {
        "PK": f"L#{cid}",
        "SK": f"{int(time.time() * 1000):013d}#{secrets.token_hex(3)}",
        "actor_name": actor_name,
        "action": action,
        "at": now_iso(),
    }
    row.update({k: v for k, v in extra.items() if v is not None})
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
    }
    for k in ("date_from", "date_to"):
        if fields.get(k):
            meta[k] = fields[k]
    _transact([
        _put(meta, "attribute_not_exists(PK)"),
        _put(_log_row(cid, actor_name, "created")),
    ])
    return meta


def duplicate_canvas(source_rows, fields, actor_name, client_id, source_name):
    """Copy a canvas's live items into a new canvas (no votes, comments, plan
    or log). Items are written first and META last, so a failure part-way
    leaves only unreachable rows, never a half-copied canvas."""
    cid = new_canvas_id()
    now = now_iso()
    live = [r for r in source_rows
            if r["SK"].startswith("ITEM#") and "removed_at" not in r][:LIVE_ITEM_CAP]
    with table().batch_writer() as batch:
        for r in live:
            item_id = r["id"] if r["kind"] == "event" else "c_" + secrets.token_urlsafe(6)
            row = {k: v for k, v in r.items() if k not in ("removed_at", "removed_by_name")}
            # Keep who added it (provenance); the copy's maker is its client.
            row.update(PK=_pk(cid), SK=f"ITEM#{item_id}", id=item_id,
                       added_by_client=client_id, added_at=now)
            batch.put_item(Item=row)
    meta = {
        "PK": _pk(cid), "SK": "META", "id": cid,
        "name": fields["name"], "note": fields.get("note") or "",
        "version": 1, "item_count": len(live), "comment_count": 0,
        "created_at": now, "updated_at": now,
        "created_by_name": actor_name, "created_by_client": client_id,
    }
    for k in ("date_from", "date_to"):
        if fields.get(k):
            meta[k] = fields[k]
    _transact([
        _put(meta, "attribute_not_exists(PK)"),
        _put(_log_row(cid, actor_name, "duplicated", name=source_name)),
    ])
    return meta


def update_canvas(cid, fields, actor_name, winner_title=None):
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
    if fields.get("winner_item_id"):
        ops.append(_live_item_check(cid, fields["winner_item_id"]))
    if "winner_item_id" in fields:
        action = "picked_winner" if fields["winner_item_id"] else "cleared_winner"
    elif "name" in fields:
        action = "renamed"
    elif "note" in fields:
        action = "edited_note"
    else:
        action = "set_dates"
    log = _log_row(cid, actor_name, action, fields=sorted(fields),
                   item_title=winner_title, name=fields.get("name"))
    try:
        _transact([bump, _put(log)] + ops)
    except _TxFailed as e:
        raise NotFound("canvas" if e.failed(0) else "item") from None


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
            _put(_log_row(cid, actor_name, "added", item_id=item_id, item_title=title)),
            _put(row, "attribute_not_exists(PK)"),
        ])
        return row, True
    except _TxFailed as e:
        if e.failed(2):
            existing = get_row(cid, f"ITEM#{item_id}")
            if existing and "removed_at" in existing:
                restore_item(cid, item_id, actor_name, title)
                return get_row(cid, f"ITEM#{item_id}"), False
            return existing, False
        if e.failed(0):
            if get_meta(cid) is None:
                raise NotFound("canvas") from None
            raise CapReached(f"a canvas holds at most {LIVE_ITEM_CAP} items") from None
        raise Conflict() from None


def remove_item(cid, item_id, actor_name, title, clear_winner):
    """Soft delete. Removing the winner clears it. No-op if already removed."""
    item_update = {"Update": {
        "TableName": table().name,
        "Key": {"PK": _pk(cid), "SK": f"ITEM#{item_id}"},
        "UpdateExpression": "SET removed_at = :now, removed_by_name = :who",
        "ConditionExpression": "attribute_exists(PK) AND attribute_not_exists(removed_at)",
        "ExpressionAttributeValues": {":now": now_iso(), ":who": actor_name},
    }}
    bump = _bump(cid, extra_add="item_count :neg",
                 extra_remove="winner_item_id" if clear_winner else "",
                 values={":neg": -1})
    try:
        _transact([bump, _put(_log_row(cid, actor_name, "removed", item_id=item_id,
                                       item_title=title)), item_update])
    except _TxFailed as e:
        if e.failed(0):
            raise NotFound("canvas") from None
        if e.failed(2):
            return  # already removed (or a concurrent remove won)
        raise Conflict() from None


def restore_item(cid, item_id, actor_name, title):
    item_update = {"Update": {
        "TableName": table().name,
        "Key": {"PK": _pk(cid), "SK": f"ITEM#{item_id}"},
        "UpdateExpression": "REMOVE removed_at, removed_by_name",
        "ConditionExpression": "attribute_exists(removed_at)",
    }}
    try:
        _transact([
            _bump(cid, extra_add="item_count :one", condition="item_count < :cap",
                  values={":cap": LIVE_ITEM_CAP}),
            _put(_log_row(cid, actor_name, "restored", item_id=item_id, item_title=title)),
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
            _put(_log_row(cid, name, "voted", item_id=item_id, item_title=title)),
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
            _put(_log_row(cid, name, "commented", item_id=item_id, item_title=title)),
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


def delete_comment(cid, item_id, comment_id, actor_name, title):
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
            _put(_log_row(cid, actor_name, "deleted_comment", item_id=item_id, item_title=title)),
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
