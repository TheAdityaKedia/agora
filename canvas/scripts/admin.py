"""Operator tools for canvases (no in-app moderation in v1).

    python canvas/scripts/admin.py --table agora-canvas-prod show <canvasId>
    python canvas/scripts/admin.py --table agora-canvas-prod delete <canvasId>

`delete` is a hard delete of the canvas and its activity log — use it when a
canvas is reported. Uses AWS credentials/region from the environment. Prints
counts, not contents, so names and comments don't end up in your terminal
history by accident (`show --full` prints them).
"""
import argparse
import json
from collections import Counter

import boto3
from boto3.dynamodb.conditions import Key


def rows(table, pk):
    kwargs = {"KeyConditionExpression": Key("PK").eq(pk)}
    while True:
        r = table.query(**kwargs)
        yield from r["Items"]
        if "LastEvaluatedKey" not in r:
            return
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    show = sub.add_parser("show")
    show.add_argument("canvas_id")
    show.add_argument("--full", action="store_true")
    delete = sub.add_parser("delete")
    delete.add_argument("canvas_id")
    delete.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()

    table = boto3.resource("dynamodb").Table(args.table)
    content = list(rows(table, f"C#{args.canvas_id}"))
    log = list(rows(table, f"L#{args.canvas_id}"))
    if not content:
        raise SystemExit("no such canvas")
    kinds = Counter(r["SK"].split("#", 1)[0] for r in content)
    print(f"canvas {args.canvas_id}: {dict(kinds)}, {len(log)} log rows")

    if args.cmd == "show" and args.full:
        for r in content + log:
            print(json.dumps(r, default=str, ensure_ascii=False))
    elif args.cmd == "delete":
        if not args.yes and input("hard-delete this canvas? type 'delete': ") != "delete":
            raise SystemExit("aborted")
        with table.batch_writer() as batch:
            for r in content + log:
                batch.delete_item(Key={"PK": r["PK"], "SK": r["SK"]})
        print(f"deleted {len(content) + len(log)} rows")


if __name__ == "__main__":
    main()
