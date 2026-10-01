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


def test_truncated_tool_call_is_reported_not_silently_empty(capsys):
    class Truncated(FakeClient):
        def converse(self, **kw):
            self.calls.append(kw)
            return {"stopReason": "max_tokens", "output": {"message": {"content": [
                {"toolUse": {"name": extract.TOOL_NAME, "input": {}}}]}}}

    client = Truncated()
    assert extract.extract_events(client, text="long newsletter", now=NOW) == []
    assert "max_tokens" in capsys.readouterr().out
    assert client.calls[0]["inferenceConfig"]["maxTokens"] >= 8000


class AssessClient(FakeClient):
    def __init__(self, events, assessment):
        super().__init__(events)
        self.assessment = assessment

    def converse(self, **kw):
        self.calls.append(kw)
        name = kw["toolConfig"]["tools"][0]["toolSpec"]["name"]
        payload = {"events": self.events, "image_assessment": self.assessment} \
            if name == extract.TOOL_NAME else self.assessment
        return {"output": {"message": {"content": [{"toolUse": {"name": name, "input": payload}}]}}}


def test_extract_image_returns_events_and_assessment():
    a = {"kind": "photo_of_flyer", "personal_info_visible": False, "bystanders_visible": True,
         "flyer_box": {"left": 0.1, "top": 0.2, "right": 0.9, "bottom": 0.8}}
    client = AssessClient([{"title": "Harvest Fest"}], a)
    events, assessment = extract.extract_image(client, ("image/png", png_bytes()), now=NOW)
    assert events[0]["title"] == "Harvest Fest" and assessment == a
    props = client.calls[0]["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]["properties"]
    assert "image_assessment" in props


def test_assess_image_classifies_only():
    a = {"kind": "designed_flyer", "personal_info_visible": False, "bystanders_visible": False,
         "flyer_box": None}
    client = AssessClient([], a)
    assert extract.assess_image(client, ("image/png", png_bytes())) == a
    assert client.calls[0]["toolConfig"]["tools"][0]["toolSpec"]["name"] == extract.ASSESS_TOOL
