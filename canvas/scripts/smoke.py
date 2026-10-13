"""Post-deploy smoke test: create → add an event → plan together → vote →
show a copy → read the view-only page.

    python canvas/scripts/smoke.py https://<function-url-id>.lambda-url.<region>.on.aws
    python canvas/scripts/smoke.py http://localhost:8787    # local_server.py

Stdlib only. Leaves one "CI smoke" canvas behind (run it against dev).
"""
import json
import sys
import urllib.request
import uuid

MANIFEST_URL = "https://theadityakedia.github.io/agora/events.json"
CLIENT = "smoke-" + uuid.uuid4().hex[:12]


def call(base, method, path, body=None):
    req = urllib.request.Request(
        base + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-Agora-Client": CLIENT},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.load(r)


def main(base):
    base = base.rstrip("/")
    with urllib.request.urlopen(MANIFEST_URL, timeout=30) as r:
        event_id = json.load(r)["events"][0]["id"]
    status, out = call(base, "POST", "/canvases", {"name": "CI smoke", "actor_name": "CI"})
    assert status == 201, (status, out)
    cid = out["canvas"]["id"]
    status, out = call(base, "POST", f"/canvases/{cid}/items",
                       {"event_id": event_id, "actor_name": "CI"})
    assert status == 201, (status, out)
    item_id = out["item"]["id"]
    # New collections are plain lists; votes need planning on.
    status, _ = call(base, "PATCH", f"/canvases/{cid}", {"planning": True})
    assert status == 200
    status, _ = call(base, "PUT", f"/canvases/{cid}/items/{item_id}/vote", {"name": "CI"})
    assert status == 200
    status, out = call(base, "GET", f"/canvases/{cid}")
    assert status == 200 and out["items"][0]["votes"][0]["name"] == "CI", out
    # Show a plain copy and read it through its view-only link.
    status, out = call(base, "POST", f"/canvases/{cid}/duplicate", {"name": "CI smoke list"})
    assert status == 201, (status, out)
    copy = out["canvas"]["id"]
    status, out = call(base, "POST", f"/canvases/{copy}/show", {"show_as": "CI"})
    assert status == 200, (status, out)
    status, out = call(base, "GET", f"/views/{out['view_id']}")
    assert status == 200 and out["view"]["show_as"] == "CI" and len(out["items"]) == 1, out
    print(f"smoke OK: canvas {cid}, shown copy {copy}, event {event_id}")


if __name__ == "__main__":
    main(sys.argv[1])
