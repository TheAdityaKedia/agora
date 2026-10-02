import sys

import admin
import store
from conftest import EVENT_IDS


def test_delete_removes_canvas_and_log(api, canvas, monkeypatch, capsys):
    api("POST", f"/canvases/{canvas}/items", {"event_id": EVENT_IDS[0], "actor_name": "Adi"})
    monkeypatch.setattr(sys, "argv", ["admin", "--table", "agora-canvas-test",
                                      "delete", canvas, "--yes"])
    admin.main()
    assert "deleted 4 rows" in capsys.readouterr().out  # META, ITEM, 2 log rows
    assert store.load_canvas(canvas) is None
    assert api("GET", f"/canvases/{canvas}")["statusCode"] == 404
