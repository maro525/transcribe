"""Unit tests for src/discourse_llm.py — Claude extractor with a mocked SDK.

No anthropic install, no network, no API key: a fake client is injected and
the request shape / parsing / failure-to-None behaviour is asserted.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import discourse_llm  # noqa: E402
from src.discourse import Utterance  # noqa: E402


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Message:
    def __init__(self, text):
        self.content = [_Block(text)]


class _Stream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class _Messages:
    def __init__(self, message, error):
        self._message = message
        self._error = error
        self.last_request = None

    def stream(self, **kwargs):
        self.last_request = kwargs
        if self._error is not None:
            raise self._error
        return _Stream(self._message)


class FakeClient:
    def __init__(self, payload=None, *, text=None, error=None):
        if text is None:
            text = json.dumps(payload) if payload is not None else "{}"
        self.messages = _Messages(_Message(text), error)


VALID_PAYLOAD = {
    "statements": [
        {"id": "s1", "utterance_index": 0, "speaker": "A", "text": "設計を決める"},
        {"id": "s2", "utterance_index": 1, "speaker": "B", "text": "予算を確認"},
    ],
    "relations": [
        {"source": "s1", "target": "s2", "type": "elaborates", "confidence": 1.5},
    ],
    "topics": [
        {"id": "t1", "label": "設計", "summary": "設計方針の議論", "statement_ids": ["s1", "s2"]}
    ],
}

UTTS = [
    Utterance(0, "A", 0.0, 1.0, "設計を決める"),
    Utterance(1, "B", 1.0, 2.0, "予算を確認"),
]


def test_build_request_shape():
    ext = discourse_llm.ClaudeDiscourseExtractor(
        client=FakeClient(VALID_PAYLOAD), model="claude-opus-4-8", effort="high"
    )
    req = ext.build_request(UTTS)
    assert req["model"] == "claude-opus-4-8"
    assert req["thinking"] == {"type": "adaptive"}
    assert req["output_config"]["effort"] == "high"
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    # forbidden on claude-opus-4-8 — must not be present
    for bad in ("temperature", "top_p", "top_k", "budget_tokens"):
        assert bad not in req


def test_extract_parses_and_clamps_confidence():
    ext = discourse_llm.ClaudeDiscourseExtractor(client=FakeClient(VALID_PAYLOAD))
    result = ext.extract(UTTS)
    assert result is not None
    assert {s.id for s in result.statements} == {"s1", "s2"}
    assert result.relations[0].confidence == 1.0  # clamped from 1.5
    assert result.relations[0].evidence == {"rule": "llm"}
    assert {t.label for t in result.topics} == {"設計"}
    assert result.topics[0].summary == "設計方針の議論"


def test_extract_returns_none_on_api_error():
    ext = discourse_llm.ClaudeDiscourseExtractor(
        client=FakeClient(error=RuntimeError("boom"))
    )
    assert ext.extract(UTTS) is None


def test_extract_returns_none_on_bad_json():
    ext = discourse_llm.ClaudeDiscourseExtractor(client=FakeClient(text="not json {"))
    assert ext.extract(UTTS) is None


def test_extract_returns_none_on_missing_keys():
    ext = discourse_llm.ClaudeDiscourseExtractor(
        client=FakeClient(payload={"statements": []})  # missing relations/topics
    )
    assert ext.extract(UTTS) is None


def test_schema_all_objects_closed():
    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(discourse_llm.EXTRACTION_SCHEMA)


def test_select_extractor_without_key_is_fallback():
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        ext = discourse_llm.select_discourse_extractor()
        assert ext.describe()["name"] == "fallback"
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
