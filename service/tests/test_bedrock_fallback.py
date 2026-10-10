"""The 4.5 fallbacks: used when the 5.5 models refuse (the account's 5.5
quotas are 0), called with the settings 4.5 needs, and skipped once refused."""
import pytest

import classify
from classify import MODELS, classify_show
from ingest import extract


class AccessDeniedException(Exception):
    """Same class name botocore gives Bedrock's refusal."""


@pytest.fixture(autouse=True)
def fresh_process():
    classify._unavailable.clear()
    yield
    classify._unavailable.clear()


class Bedrock:
    """Refuses `denied` models; records every call's kwargs; answers the rest
    with a classify reply or an extract tool call."""

    def __init__(self, denied=()):
        self.denied, self.calls = set(denied), []

    def converse(self, **kw):
        self.calls.append(kw)
        if kw["modelId"] in self.denied:
            raise AccessDeniedException(f"{kw['modelId']} is not available for this account.")
        if "toolConfig" in kw:
            name = kw["toolConfig"]["tools"][0]["toolSpec"]["name"]
            return {"output": {"message": {"content": [{"toolUse": {"name": name, "input": {
                "events": [{"title": "X"}], "safe_to_publish": True}}}]}}}
        return {"output": {"message": {"content": [{"text": '{"types":[["talk"]],"topics":[],"cost":"free"}'}]}}}


HAIKU_55 = ("global.anthropic.claude-haiku-5-5", "us.anthropic.claude-haiku-5-5")
SONNET_55 = ("global.anthropic.claude-sonnet-5-5", "us.anthropic.claude-sonnet-5-5")


def test_the_chain_is_haiku_55_then_haiku_45():
    assert MODELS == HAIKU_55 + ("global.anthropic.claude-haiku-4-5-20251001-v1:0",
                                 "us.anthropic.claude-haiku-4-5-20251001-v1:0")
    assert all(m.startswith(("global.", "us.")) for m in MODELS)  # inference profiles


def test_tagging_falls_back_to_haiku_45_when_55_is_refused():
    b = Bedrock(denied=HAIKU_55)
    c = classify_show("A Talk", "City Lights Booksellers", "A reading.", client=b)
    assert c.model == "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert [k["modelId"] for k in b.calls] == list(MODELS[:3])


def test_each_model_gets_its_own_settings():
    """5.5 rejects temperature and takes effort; 4.5 takes temperature 0 and
    would reject the 5.5-only field."""
    b = Bedrock(denied=HAIKU_55)
    classify_show("A Talk", "City Lights Booksellers", "A reading.", client=b)
    k55, k45 = b.calls[0], b.calls[2]
    assert "temperature" not in k55["inferenceConfig"]
    assert k55["additionalModelRequestFields"] == {"output_config": {"effort": "low"}}
    assert k45["inferenceConfig"] == {"maxTokens": 250, "temperature": 0.0}
    assert "additionalModelRequestFields" not in k45


def test_a_refused_model_is_skipped_for_the_rest_of_the_run():
    """Without this, every show in a run spends two round trips on 5.5 first."""
    b = Bedrock(denied=HAIKU_55)
    classify_show("One", "S", None, client=b)
    b.calls.clear()
    classify_show("Two", "S", None, client=b)
    assert [k["modelId"] for k in b.calls] == [MODELS[2]]   # straight to 4.5


def test_only_access_denied_marks_a_model_unavailable():
    """A throttle or a timeout is transient: the model is tried again next call."""
    class Flaky(Bedrock):
        def converse(self, **kw):
            self.calls.append(kw)
            if kw["modelId"] == MODELS[0] and len(self.calls) == 1:
                raise TimeoutError("read timed out")
            return super().converse(**kw)
    b = Flaky()
    classify_show("One", "S", None, client=b)
    b.calls.clear()
    classify_show("Two", "S", None, client=b)
    assert b.calls[0]["modelId"] == MODELS[0]   # 5.5 is still first


def test_when_everything_was_refused_the_real_errors_are_still_reported():
    b = Bedrock(denied=MODELS)
    with pytest.raises(RuntimeError):
        classify_show("One", "S", None, client=b)
    with pytest.raises(RuntimeError) as e:   # all are in _unavailable now
        classify_show("Two", "S", None, client=b)
    assert all(m in str(e.value) for m in MODELS)


def test_email_extraction_falls_back_to_haiku_45_with_a_forced_tool():
    b = Bedrock(denied=HAIKU_55)
    from datetime import datetime, timezone
    events = extract.extract_events(b, text="x", now=datetime(2026, 10, 9, tzinfo=timezone.utc))
    assert events[0]["title"] == "X"
    k45 = b.calls[-1]
    assert k45["modelId"] == MODELS[2]
    assert k45["toolConfig"]["toolChoice"] == {"tool": {"name": extract.TOOL_NAME}}
    assert k45["inferenceConfig"]["temperature"] == 0.0


def test_image_check_falls_back_to_sonnet_45_never_to_haiku():
    assert extract.VERIFY_MODELS == SONNET_55 + (
        "global.anthropic.claude-sonnet-4-5-20250929-v1:0", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    assert not any("haiku" in m for m in extract.VERIFY_MODELS)


def test_sonnet_45_gets_a_forced_tool_and_sonnet_55_auto():
    assert extract._tool_choice("global.anthropic.claude-sonnet-5-5", "t")[0] == {"auto": {}}
    assert extract._tool_choice("global.anthropic.claude-sonnet-4-5-20250929-v1:0", "t")[0] == \
        {"tool": {"name": "t"}}
