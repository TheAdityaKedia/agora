import json
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "api"))
sys.path.insert(0, str(HERE.parents[0] / "scripts"))

import handler  # noqa: E402
import snapshot  # noqa: E402
import store  # noqa: E402
from local_server import create_table  # noqa: E402

FIXTURE = HERE / "fixtures" / "events.json"
EVENT_IDS = [e["id"] for e in json.loads(FIXTURE.read_text())["events"]]
ORIGIN = "https://theadityakedia.github.io"


@pytest.fixture(autouse=True)
def aws(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_NAME", "agora-canvas-test")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    monkeypatch.setenv("ALLOW_LOCALHOST", "false")
    monkeypatch.setenv("MANIFEST_URL", FIXTURE.as_uri())
    with mock_aws():
        create_table(boto3.client("dynamodb"), "agora-canvas-test")
        store.reset_table_cache()
        snapshot.reset_cache()
        yield


class Api:
    """Call the Lambda handler the way a Function URL would."""

    def __init__(self, client="client-aaaa1111", ip="1.2.3.4"):
        self.client, self.ip = client, ip

    def __call__(self, method, path, body=None, *, query=None, origin=ORIGIN, headers=None):
        h = {"origin": origin} if origin else {}
        if self.client:
            h["X-Agora-Client"] = self.client
        h.update(headers or {})
        event = {
            "rawPath": path,
            "queryStringParameters": query,
            "headers": h,
            "body": json.dumps(body) if body is not None else None,
            "isBase64Encoded": False,
            "requestContext": {"http": {"method": method, "sourceIp": self.ip}},
        }
        resp = handler.lambda_handler(event, None)
        resp["json"] = json.loads(resp["body"]) if resp["body"] else None
        return resp


@pytest.fixture
def api():
    return Api()


@pytest.fixture
def canvas(api):
    r = api("POST", "/canvases", {"name": "Adi & Sam hangout", "actor_name": "Adi",
                                  "date_from": "2026-10-10", "date_to": "2026-10-12"})
    assert r["statusCode"] == 201, r["json"]
    return r["json"]["canvas"]["id"]
