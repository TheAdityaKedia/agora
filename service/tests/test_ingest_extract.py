from datetime import datetime, timezone

import pytest

from ingest import extract
from tests.emailfixtures import png_bytes

NOW = datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, events=None, fail_models=()):
        self.events, self.fail_models, self.calls = events or [], set(fail_models), []

    def converse(self, **kw):
        self.calls.append(kw)
        if kw["modelId"] in self.fail_models:
            raise RuntimeError(f"boom {kw['modelId']}")
        return {"output": {"message": {"content": [
            {"toolUse": {"name": extract.TOOL_NAME, "input": {"events": self.events}}}]}}}


def test_text_call_forces_the_tool_and_returns_normalized_events():
    client = FakeClient([{"title": "Verses & Vinyl", "date": "2026-10-16", "start_time": "19:30"}])
    events = extract.extract_events(client, text="Verses & Vinyl, Fri Oct 16 7:30pm", now=NOW)
    assert events == [{"title": "Verses & Vinyl", "date": "2026-10-16", "start_time": "19:30",
                       "end_time": None, "venue": None, "address": None, "description": None,
                       "cost_text": None, "url": None, "recurrence": None}]
    kw = client.calls[0]
    assert kw["toolConfig"]["toolChoice"] == {"tool": {"name": extract.TOOL_NAME}}
    assert "2026-09-29" in kw["system"][0]["text"]  # today's date in the prompt
    assert "America/Los_Angeles" in kw["system"][0]["text"]


def test_image_call_sends_an_image_block():
    client = FakeClient([])
    extract.extract_events(client, image=("image/png", png_bytes()), now=NOW)
    content = client.calls[0]["messages"][0]["content"]
    assert content[0]["image"]["format"] == "png" and content[0]["image"]["source"]["bytes"]


def test_caps_at_twenty_events():
    client = FakeClient([{"title": f"E{i}"} for i in range(30)])
    assert len(extract.extract_events(client, text="many", now=NOW)) == 20


def test_falls_back_and_reports_every_model_error():
    client = FakeClient([{"title": "X"}], fail_models=[extract.PRIMARY_MODEL])
    assert extract.extract_events(client, text="x", now=NOW)[0]["title"] == "X"
    both = FakeClient(fail_models=[extract.PRIMARY_MODEL, extract.FALLBACK_MODEL])
    with pytest.raises(RuntimeError) as e:
        extract.extract_events(both, text="x", now=NOW)
    assert extract.PRIMARY_MODEL in str(e.value) and extract.FALLBACK_MODEL in str(e.value)


def test_prepare_image_downscales_large_images_under_bedrock_limits():
    fmt, data = extract.prepare_image("image/png", png_bytes(9000, 3000, noise=False))
    from io import BytesIO
    from PIL import Image
    with Image.open(BytesIO(data)) as im:
        assert max(im.size) <= 8000
    assert len(data) <= 3_750_000 and fmt in {"png", "jpeg"}


def test_prepare_image_rejects_garbage():
    assert extract.prepare_image("image/png", b"not an image") is None


def test_prompt_merges_sub_sessions_and_protects_personal_details():
    client = FakeClient([])
    extract.extract_events(client, text="x", now=NOW)
    system = client.calls[0]["system"][0]["text"]
    assert "ONE event" in system and "classes" in system.lower()
    assert "phone numbers" in system and "email addresses" in system
