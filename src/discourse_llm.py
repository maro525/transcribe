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
    ARG_STANCE,
    Argument,
    DecisionFlow,
    DiscourseExtraction,
    FLOW_CONFIDENCE,
    FallbackRelationExtractor,
    OPTION_STATUS,
    OUTCOME_KIND,
    OUTCOME_STATUS,
    Option,
    Outcome,
    Question,
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
                    "summary": {"type": "string"},
                    "statement_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "label", "summary", "statement_ids"],
                "additionalProperties": False,
            },
        },
        # Optional decision-flow layer (D1). NOT in the top-level "required"
        # list, so a response that omits it still validates. Every nested
        # object is closed; nullable scalars use ["string","null"]; ranges are
        # avoided (confidence is an enum). Cross-reference integrity is checked
        # client-side in discourse.validate_decision_flows (fail-soft).
        "decision_flows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic_id": {"type": "string"},
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "summary": {"type": "string"},
                                "statement_id": {"type": ["string", "null"]},
                            },
                            "required": ["id", "summary", "statement_id"],
                            "additionalProperties": False,
                        },
                    },
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "label": {"type": "string"},
                                "summary": {"type": "string"},
                                "statement_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "introduced_by": {"type": ["string", "null"]},
                                "status": {"type": "string", "enum": list(OPTION_STATUS)},
                            },
                            "required": [
                                "id",
                                "label",
                                "summary",
                                "statement_ids",
                                "introduced_by",
                                "status",
                            ],
                            "additionalProperties": False,
                        },
                    },
                    "arguments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "statement_id": {"type": "string"},
                                "option_id": {"type": "string"},
                                "stance": {"type": "string", "enum": list(ARG_STANCE)},
                            },
                            "required": ["id", "statement_id", "option_id", "stance"],
                            "additionalProperties": False,
                        },
                    },
                    "outcome": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "enum": list(OUTCOME_STATUS)},
                            "kind": {"type": "string", "enum": list(OUTCOME_KIND)},
                            "summary": {"type": "string"},
                            "statement_id": {"type": ["string", "null"]},
                            "selected_option_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "rationale_statement_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": [
                            "status",
                            "kind",
                            "summary",
                            "statement_id",
                            "selected_option_ids",
                            "rationale_statement_ids",
                        ],
                        "additionalProperties": False,
                    },
                    "confidence": {"type": "string", "enum": list(FLOW_CONFIDENCE)},
                },
                "required": [
                    "topic_id",
                    "questions",
                    "options",
                    "arguments",
                    "outcome",
                    "confidence",
                ],
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
statement の text はその論点を凝縮した**短い要約**(10〜20字程度、体言止め推奨)にする。\
逐語をなぞらず要点だけを残すこと。utterance_index は入力で示された発話番号、id は "s1" からの連番。
2. statement 間の論理関係を抽出する。型と方向の規約:
   - supports: 根拠 → 主張(source が根拠)
   - causes: 原因 → 結果(source が原因)
   - elaborates: 元の発言 → 詳細化・要約(source が元)
   - contrasts: 後の発言 → 先行する発言(source が後の発言)
   confidence は 0〜1 の実数。確信のない関係は出力しない(高精度優先)。
3. **すべての statement をいずれかの話題(topic)に必ず割り当てる**。「その他」的な\
受け皿は作らず、内容の近い statement をまとめること。label は短い日本語名詞句、\
summary はその話題で何が話し合われたかを 1 文(40〜60字程度)にまとめた要約、\
statement_ids は所属 statement の id 列。1 つの statement は 1 つの topic のみに属する。
4. **決定を要する話題についてのみ** decision_flows を抽出する(任意)。中心的な問い(question)、\
競合する選択肢(option)、各選択肢への賛否(argument)、収束(outcome)を、既出の statement id を\
参照して構成する。原則:
   - **不確実なら省略する**。すべての話題を無理に決定の形にしない(情報共有だけの話題は出さない)。
   - option は実際に提案された案・候補のみ。数は少なめを優先し、投機的な選択肢を作らない。
   - option.introduced_by はその案が最初に現れた statement id、statement_ids は関連する id 列。
   - argument.stance は pro/con/neutral。option_id で対象の option を指す。
   - outcome.status は decided(決定)/deferred(明示的な先送りのみ)/open(未決)。\
kind は single_option/hybrid(複数採用)/no_option/unknown。decided かつ single_option は\
selected_option_ids をちょうど 1 つ、hybrid は 2 つ以上にする。
   - option.status は selected/rejected/abandoned/unresolved/partial。
   - 参照する statement id は必ず手順 1 で出力済みのものを使う。確信のない関係・決定は出さない。

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
            summary=str(item.get("summary", "")),
        )
        for item in data["topics"]
    )
    return DiscourseExtraction(
        statements=statements,
        relations=relations,
        topics=topics,
        decision_flows=_parse_decision_flows(data),
    )


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _parse_decision_flows(data: dict[str, Any]) -> tuple[DecisionFlow, ...]:
    """Tolerant, isolated parse of the optional decision-flow layer.

    Never raises: a malformed flow is skipped so it can't take down the base
    extraction (statements/relations/topics). Cross-reference/enum integrity is
    enforced later by discourse.validate_decision_flows (drops bad flows)."""
    raw = data.get("decision_flows")
    if not isinstance(raw, list):
        return ()
    flows: list[DecisionFlow] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            flows.append(_parse_one_flow(item))
        except Exception:  # malformed flow -> drop, keep the rest
            continue
    return tuple(flows)


def _parse_one_flow(item: dict[str, Any]) -> DecisionFlow:
    questions = tuple(
        Question(
            id=str(q["id"]),
            summary=str(q.get("summary", "")),
            statement_id=_opt_str(q.get("statement_id")),
        )
        for q in item.get("questions", []) or []
    )
    options = tuple(
        Option(
            id=str(o["id"]),
            label=str(o.get("label", "")),
            summary=str(o.get("summary", "")),
            statement_ids=tuple(str(s) for s in o.get("statement_ids", []) or []),
            introduced_by=_opt_str(o.get("introduced_by")),
            status=str(o.get("status", "unresolved")),
        )
        for o in item.get("options", []) or []
    )
    arguments = tuple(
        Argument(
            id=str(a["id"]),
            statement_id=str(a.get("statement_id", "")),
            option_id=str(a.get("option_id", "")),
            stance=str(a.get("stance", "neutral")),
        )
        for a in item.get("arguments", []) or []
    )
    outcome_data = item.get("outcome")
    outcome = None
    if isinstance(outcome_data, dict):
        outcome = Outcome(
            status=str(outcome_data.get("status", "open")),
            kind=str(outcome_data.get("kind", "unknown")),
            summary=str(outcome_data.get("summary", "")),
            statement_id=_opt_str(outcome_data.get("statement_id")),
            selected_option_ids=tuple(
                str(x) for x in outcome_data.get("selected_option_ids", []) or []
            ),
            rationale_statement_ids=tuple(
                str(x) for x in outcome_data.get("rationale_statement_ids", []) or []
            ),
        )
    return DecisionFlow(
        topic_id=str(item.get("topic_id", "")),
        questions=questions,
        options=options,
        arguments=arguments,
        outcome=outcome,
        confidence=str(item.get("confidence", "medium")),
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
