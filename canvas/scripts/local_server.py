"""Run the canvas API locally, for frontend work and smoke tests.

    python canvas/scripts/local_server.py --moto            # in-memory DynamoDB
    python canvas/scripts/local_server.py --table agora-canvas-dev   # real table

--moto needs `moto[dynamodb]` (canvas/requirements-dev.txt). By default event
snapshots come from the local frontend/events.json, so no network is needed.
Serves http://localhost:8787 and allows any localhost origin (CORS).
"""
import argparse
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "canvas" / "api"))


def create_table(client, name):
    """The table canvas/infra/stacks.py defines, for moto / local use."""
    client.create_table(
        TableName=name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                              {"AttributeName": "SK", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"},
                   {"AttributeName": "SK", "KeyType": "RANGE"}],
    )
    client.update_time_to_live(TableName=name, TimeToLiveSpecification={
        "Enabled": True, "AttributeName": "ttl"})


def make_handler(lambda_handler):
    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            parts = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            event = {
                "rawPath": parts.path,
                "queryStringParameters": dict(parse_qsl(parts.query)) or None,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": self.rfile.read(length).decode() if length else None,
                "isBase64Encoded": False,
                "requestContext": {"http": {"method": self.command,
                                            "sourceIp": self.client_address[0]}},
            }
            resp = lambda_handler(event, None)
            body = resp["body"].encode()
            self.send_response(resp["statusCode"])
            for k, v in resp["headers"].items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _serve

        def log_message(self, *args):
            pass  # handler prints its own one-line log

    return Handler


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--moto", action="store_true", help="in-memory DynamoDB via moto")
    g.add_argument("--table", help="use this real DynamoDB table (AWS creds from env)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--manifest", default=(ROOT / "frontend" / "events.json").as_uri(),
                    help="events.json URL used for snapshots (default: local file)")
    ap.add_argument("--index", help="event-index.json URL (default: beside the manifest; "
                                    "if missing, the manifest is used, with no overlay)")
    ap.add_argument("--cache-ttl", type=int, help="seconds to cache the index (default 600)")
    args = ap.parse_args()

    os.environ["MANIFEST_URL"] = args.manifest
    if args.index:
        os.environ["INDEX_URL"] = args.index
    if args.cache_ttl is not None:
        os.environ["SNAPSHOT_CACHE_TTL_S"] = str(args.cache_ttl)
    os.environ["ALLOW_LOCALHOST"] = "true"
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
    if args.moto:
        from moto import mock_aws
        mock_aws().start()
        import boto3
        os.environ["TABLE_NAME"] = "agora-canvas-local"
        create_table(boto3.client("dynamodb"), "agora-canvas-local")
    else:
        os.environ["TABLE_NAME"] = args.table

    from handler import lambda_handler
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(lambda_handler))
    print(f"canvas API on http://localhost:{args.port} (table {os.environ['TABLE_NAME']})")
    server.serve_forever()


if __name__ == "__main__":
    main()
