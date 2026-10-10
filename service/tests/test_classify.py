"""Tests for the LLM classifier (service/classify.py). No live Bedrock calls."""
import json

import pytest

import classify
from classify import build_prompt, parse_classification, classify_show, PRIMARY_MODEL, FALLBACK_MODEL


# ---- prompt --------------------------------------------------------------

def test_build_prompt_includes_taxonomy_event_and_source_profile():
    system, user = build_prompt(
        title="Branford Marsalis Quartet",
        source="SFJAZZ Center",
        description="The saxophonist's quartet performs.",
    )
    # taxonomy axes present
    assert "TYPE options" in user and "TOPIC options" in user
    assert "poetry" in user and "performance" in user
    # event fields present
    assert "Branford Marsalis Quartet" in user
    assert "SFJAZZ Center" in user
    # the venue profile prior is injected (SFJAZZ profile mentions jazz)
    assert "jazz" in user.lower()
    # system prompt states the JSON contract
    assert "types" in system and "topics" in system and "cost" in system


def test_build_prompt_tolerates_source_without_profile():
    system, user = build_prompt(title="X", source="Unknown Venue", description="d")
    assert "Unknown Venue" in user  # no profile, but source still shown


# ---- parse ---------------------------------------------------------------

def test_parse_valid_json():
    c = parse_classification('{"types":[["performance"]],"topics":["jazz"],"cost":"paid"}')
    assert c["types"] == [["performance"]]
    assert c["topics"] == ["jazz"]
    assert c["cost"] == "paid"


def test_parse_coerces_off_taxonomy_and_dedupes():
    raw = ('{"types":[["talk","bogus"],["talk","bogus"],["nonsense"]],'
           '"topics":["poetry","madeup","poetry"],"cost":"free"}')
    c = parse_classification(raw)
    # unknown leaf -> parent, duplicate collapsed, unknown top dropped
    assert c["types"] == [["talk"]]
    # unknown topic dropped, dupe collapsed
    assert c["topics"] == ["poetry"]
    assert c["cost"] == "free"


def test_parse_extracts_json_embedded_in_prose():
    c = parse_classification('Here you go:\n{"types":[["screening"]],"topics":["film"],"cost":"unknown"} done')
    assert c["types"] == [["screening"]]
    assert c["topics"] == ["film"]


def test_parse_bad_cost_falls_back_to_unknown():
    c = parse_classification('{"types":[["talk"]],"topics":[],"cost":"cheap"}')
    assert c["cost"] == "unknown"


def test_parse_garbage_returns_empty_types():
    c = parse_classification("not json at all")
    assert c["types"] == []
    assert c["topics"] == []
    assert c["cost"] == "unknown"


# ---- classify_show orchestration (fake Bedrock client) -------------------

def _converse_reply(text):
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "usage": {"inputTokens": 100, "outputTokens": 20},
    }


class _FakeClient:
    """Records modelIds it was called with; returns a canned reply."""
    def __init__(self, reply_text, fail_models=()):
        self.reply_text = reply_text
        self.fail_models = set(fail_models)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs["modelId"])
        if kwargs["modelId"] in self.fail_models:
            raise RuntimeError("model unavailable")
        return _converse_reply(self.reply_text)


def test_classify_show_returns_classification_via_primary():
    client = _FakeClient('{"types":[["performance"]],"topics":["jazz"],"cost":"paid"}')
    c = classify_show("Branford Marsalis Quartet", "SFJAZZ Center", "Jazz quartet.",
                      client=client)
    assert c.types == [["performance"]]
    assert c.topics == ["jazz"]
    assert c.cost == "paid"
    assert c.model == PRIMARY_MODEL
    assert c.source == "SFJAZZ Center"
    assert c.classified_at  # timestamp set
    # primary tried first
    assert client.calls[0] == PRIMARY_MODEL


def test_classify_show_falls_back_when_primary_fails():
    client = _FakeClient('{"types":[["talk"]],"topics":[],"cost":"free"}',
                         fail_models=[PRIMARY_MODEL])
    c = classify_show("A Talk", "City Lights Booksellers", "A reading.", client=client)
    assert c.model == FALLBACK_MODEL
    assert client.calls == [PRIMARY_MODEL, FALLBACK_MODEL]


def test_classify_show_error_names_every_failed_model():
    """A fallback's error must not hide the primary's (the real cause)."""
    import pytest
    from classify import MODELS
    client = _FakeClient("{}", fail_models=MODELS)
    with pytest.raises(RuntimeError) as exc:
        classify_show("A Talk", "City Lights Booksellers", "A reading.", client=client)
    assert all(m in str(exc.value) for m in MODELS)


def test_fallback_is_an_inference_profile():
    # Call Haiku through an inference profile, not its bare foundation-model id.
    assert FALLBACK_MODEL.startswith("us.anthropic.")


def test_keyword_topics_from_title():
    from classify import keyword_topics
    assert "trivia" in keyword_topics("Trivia Night at Abbey Tavern")
    assert "trivia" in keyword_topics("Pub Quiz at The Bar")
    assert "karaoke" in keyword_topics("Karaoke Tuesdays at X")
    assert "bingo" in keyword_topics("Drag Bingo at Y") and "drag" in keyword_topics("Drag Bingo at Y")
    assert keyword_topics("An Evening of Jazz") == []


def test_classify_show_always_includes_title_keyword_topics():
    # Even if the model omits it, a title-guaranteed topic is added.
    client = _FakeClient('{"types":[["social"]],"topics":[],"cost":"free"}')
    c = classify_show("Trivia Night at Abbey Tavern", "SF Bar Guide", "Weekly trivia.",
                      client=client)
    assert "trivia" in c.topics


def test_title_game_keyword_overrides_model_game_topic():
    # "Bingo at X" — model wrongly says trivia. Title names bingo, so trivia
    # (a confusable game topic) is dropped; bingo kept.
    client = _FakeClient('{"types":[["social"]],"topics":["trivia"],"cost":"free"}')
    c = classify_show("Bingo at The Green Heron", "SF Bar Guide", "Bingo night.",
                      client=client)
    assert "bingo" in c.topics
    assert "trivia" not in c.topics


def test_non_game_topics_survive_a_game_keyword():
    # A drag bingo keeps both (drag isn't in the mutually-exclusive game set).
    client = _FakeClient('{"types":[["social"]],"topics":["drag","trivia"],"cost":"free"}')
    c = classify_show("Drag Bingo at Y", "SF Bar Guide", "Drag bingo.", client=client)
    assert "bingo" in c.topics and "drag" in c.topics
    assert "trivia" not in c.topics


class _RecordingClient:
    def __init__(self, content):
        self.content, self.kwargs = content, None

    def converse(self, **kwargs):
        self.kwargs = kwargs
        return {"output": {"message": {"content": self.content}}, "stopReason": "end_turn"}


def test_converse_skips_reasoning_and_sends_no_temperature():
    import classify
    client = _RecordingClient([{"reasoningContent": {"reasoningText": {"text": "hmm"}}},
                               {"text": '{"cost":"free"}'}])
    assert classify._converse(client, PRIMARY_MODEL, "s", "u") == '{"cost":"free"}'
    assert "temperature" not in client.kwargs["inferenceConfig"]  # 400 on Haiku 5.5
    assert client.kwargs["additionalModelRequestFields"] == {"output_config": {"effort": "low"}}


def test_converse_without_text_raises_so_nothing_is_cached():
    import classify
    import pytest
    client = _RecordingClient([{"reasoningContent": {"reasoningText": {"text": "hmm"}}}])
    with pytest.raises(RuntimeError, match="no text"):
        classify._converse(client, PRIMARY_MODEL, "s", "u")
