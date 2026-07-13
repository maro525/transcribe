"""Claude-based discourse-structure extractor (anthropic SDK isolation).

Everything that touches the ``anthropic`` SDK lives in this module and is
imported lazily, so the rest of the app (and the bare test environment)
imports cleanly without the SDK installed. ``src/discourse.py`` stays
stdlib-only and hosts the shared data model + deterministic fallback.

API usage (validated against the claude-api reference, 2026-07):

- ``anthropic.Anthropic()`` resolves ``ANTHROPIC_API_KEY`` from the
  environment — the key is never hardcoded or passed around.
- Structured output via **streaming + json_schema**:
  ``client.messages.stream(..., output_config={"effort": ..., "format":
  {"type": "json_schema", "schema": ...}})`` → ``get_final_message()`` →
  first text block → ``json.loads``. Streaming is used because meeting-length
  output needs a large ``max_tokens`` and the SDK rejects non-streaming
  requests it estimates would exceed its timeout guard.
  (``messages.parse(output_format=...)`` is the non-streaming helper; the
  plan's "parse 優先、長大時 stream" is unified on the streaming path so one
  request shape covers all input sizes.)
- Every object in the schema carries ``additionalProperties: false``
  (structured-outputs requirement); numeric range constraints are not
  supported in the schema, so ``confidence`` is clamped client-side.
- ``thinking={"type": "adaptive"}`` (the only thinking mode on
  claude-opus-4-8; no budget_tokens / temperature / top_p / top_k).
- The shared system prompt carries ``cache_control: {"type": "ephemeral"}``.
  claude-opus-4-8's minimum cacheable prefix is 4096 tokens, so a short
  prompt silently doesn't cache — harmless; it only pays off when several
  files are processed within the 5-minute TTL.
- Any SDK error (RateLimitError / APIStatusError / APIConnectionError / ...)
  or output-validation failure is caught and converted to ``None`` — the
  caller then falls back to the deterministic extractor; jobs never ERROR.
"""
from __future__ import annotations

import json
from typing import Any

from . import config
from .discourse import (
    DiscourseExtraction,
    FallbackRelationExtractor,
    RELATION_TYPES,
    Relation,
    Statement,
    Topic,
    Utterance,
)

# JSON Schema for the structured output (mirrors the dataclasses in
# discourse.py). All objects set additionalProperties: false; range checks on
# confidence are enforced client-side (not supported in the schema).
EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "statements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "utterance_index": {"type": "integer"},
                    "speaker": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["id", "utterance_index", "speaker", "text"],
                "additionalProperties": False,
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "type": {"type": "string", "enum": list(RELATION_TYPES)},
                    "confidence": {"type": "number"},
                },
                "required": ["source", "target", "type", "confidence"],
                "additionalProperties": False,
            },
        },
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "statement_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "label", "statement_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["statements", "relations", "topics"],
    "additionalProperties": False,
}

# Shared across meetings -> cache_control candidate. Direction conventions
# must match discourse.py's Decision Log.
SYSTEM_PROMPT = """\
あなたは会議書き起こしの談話構造アナリストです。話者ラベルつきの発話列から、\
議論の論理構造を抽出してください。

手順:
1. 各発話を statement(主張・根拠・事実などの論点)に分割する。\
statement の text はその論点を短く言い換えた**要約**(20〜40字程度、体言止め可)にする。\
逐語の引用は不要。utterance_index は入力で示された発話番号、id は "s1" からの連番。
2. statement 間の論理関係を抽出する。型と方向の規約:
   - supports: 根拠 → 主張(source が根拠)
   - causes: 原因 → 結果(source が原因)
   - elaborates: 元の発言 → 詳細化・要約(source が元)
   - contrasts: 後の発言 → 先行する発言(source が後の発言)
   confidence は 0〜1 の実数。確信のない関係は出力しない(高精度優先)。
3. **すべての statement をいずれかの話題(topic)に必ず割り当てる**。「その他」的な\
受け皿は作らず、内容の近い statement をまとめること。label は短い日本語名詞句、\
statement_ids は所属 statement の id 列。1 つの statement は 1 つの topic のみに属する。

出力は指定スキーマの JSON のみ。会話にない内容を創作しないこと。"""


def _transcript_block(utterances: list[Utterance]) -> str:
    lines = ["発話列(番号 [話者] 本文):"]
    for utterance in utterances:
        lines.append(f"{utterance.index} [{utterance.speaker}] {utterance.text}")
    return "\n".join(lines)


class ClaudeDiscourseExtractor:
    """Primary extractor: Claude structured output over the whole meeting.

    A pre-built client can be injected for tests (no SDK / no network). Any
    failure — import, API, JSON, validation — yields ``None`` so the caller
    falls back deterministically.
    """

    def __init__(
        self,
        client: Any = None,
        *,
        model: str | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self._client = client
        self.model = model or config.DISCOURSE_MODEL
        self.effort = effort or config.DISCOURSE_EFFORT
        self.max_tokens = max_tokens or config.DISCOURSE_MAX_TOKENS

    def describe(self) -> dict[str, Any]:
        return {"name": "claude", "model": self.model, "effort": self.effort}

    # -- request construction (separated for testability) -------------------

    def build_request(self, utterances: list[Utterance]) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA},
            },
            "system": [
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [
                {"role": "user", "content": _transcript_block(utterances)}
            ],
        }

    def extract(self, utterances: list[Utterance]) -> DiscourseExtraction | None:
        try:
            client = self._ensure_client()
            request = self.build_request(utterances)
            with client.messages.stream(**request) as stream:
                message = stream.get_final_message()
            text = next(
                block.text
                for block in message.content
                if getattr(block, "type", None) == "text"
            )
            return _parse_extraction(json.loads(text))
        except Exception as error:  # API/JSON/validation -> fallback path
            print(f"Claude discourse extraction failed ({type(error).__name__}): {error}")
            return None

    def _ensure_client(self) -> Any:
        if self._client is None:
            import anthropic  # lazy: SDK optional at runtime

            self._client = anthropic.Anthropic()
        return self._client


def _parse_extraction(data: Any) -> DiscourseExtraction:
    """Convert parsed JSON into the stdlib data model (raises on bad shape;
    cross-reference/substring checks happen later in validate_extraction)."""
    if not isinstance(data, dict):
        raise ValueError("extraction payload is not an object")
    statements = tuple(
        Statement(
            id=str(item["id"]),
            utterance_index=int(item["utterance_index"]),
            speaker=str(item["speaker"]),
            text=str(item["text"]),
        )
        for item in data["statements"]
    )
    relations = tuple(
        Relation(
            source=str(item["source"]),
            target=str(item["target"]),
            type=str(item["type"]),
            confidence=min(1.0, max(0.0, float(item["confidence"]))),
            evidence={"rule": "llm"},
        )
        for item in data["relations"]
    )
    topics = tuple(
        Topic(
            id=str(item["id"]),
            label=str(item["label"]),
            statement_ids=tuple(str(sid) for sid in item["statement_ids"]),
        )
        for item in data["topics"]
    )
    return DiscourseExtraction(
        statements=statements, relations=relations, topics=topics
    )


def claude_available() -> bool:
    """True when both the API key (env) and the SDK are present."""
    import importlib.util
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    return importlib.util.find_spec("anthropic") is not None


def select_discourse_extractor() -> Any:
    """Default extractor factory: Claude when key+SDK are available, else the
    deterministic fallback. (Scope decision: selection is keyed on the
    ANTHROPIC_API_KEY env var only — a host authenticated via an `ant auth
    login` profile without the env var will use the fallback.)"""
    if claude_available():
        return ClaudeDiscourseExtractor()
    return FallbackRelationExtractor()
