"""Logical discourse-structure extraction over a finished transcript.

Turns a diarized utterance list into *statements* (sentence-level fragments
with speaker/utterance references), typed directed *relations* between them
(supports / causes / elaborates / contrasts) and *topics* (statement
clusters), assembled into the ``{stem}.structure.json`` v1 payload.

Design (see .claude/docs/decisions/task-LOCAL-discourse-structure-20260707.md):

- The primary extractor is Claude (``src.discourse_llm.ClaudeDiscourseExtractor``);
  this module hosts the extraction *contract* plus everything that must stay
  deterministic and dependency-free: statement splitting, the marker-based
  ``FallbackRelationExtractor`` (used when no API key / SDK / on API failure),
  topic clustering, local validation and DAG assembly.
- **stdlib only.** No anthropic, no pydantic, no janome at import time — the
  test environment runs bare. The LLM-facing Pydantic-ish schema lives as a
  JSON Schema literal in ``discourse_llm.py``, isolated with the SDK.
- Direction conventions (Decision Log): supports: 根拠→主張 / causes: 原因→結果 /
  elaborates: 元→詳細 / contrasts: 後の発言→先の発言.
- The output is "検出された構造" — detected, not ground truth; the UI says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Protocol

STRUCTURE_KIND = "logical_structure"
RELATION_TYPES = ("supports", "causes", "elaborates", "contrasts")

# Topic clustering knobs (deterministic; no env dependence for testability).
_TOPIC_MIN_EDGE_WEIGHT = 2  # prune co-occurrence edges seen fewer times
_TOPIC_MAX_COMPONENT = 12  # larger components get label propagation
_TOPIC_MAX_LABEL_PASSES = 10
_TOPIC_TERMS_PER_STATEMENT = 6


# ---------------------------------------------------------------------------
# Data model (stdlib dataclasses — the wire schema of structure.json v1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Utterance:
    """One diarized transcript segment (input unit)."""

    index: int
    speaker: str
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Statement:
    """A sentence-level fragment attributed to an utterance/speaker."""

    id: str
    utterance_index: int
    speaker: str
    text: str
    terms: tuple[str, ...] = ()
    topic_id: str | None = None


@dataclass(frozen=True)
class Relation:
    """Directed, typed relation between two statements."""

    source: str
    target: str
    type: str  # one of RELATION_TYPES
    confidence: float
    evidence: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Topic:
    id: str
    label: str
    statement_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiscourseExtraction:
    """What a RelationExtractor returns (topics may be empty -> clustered
    locally by :func:`cluster_topics`)."""

    statements: tuple[Statement, ...]
    relations: tuple[Relation, ...]
    topics: tuple[Topic, ...] = ()


class RelationExtractor(Protocol):
    """Extraction contract. ``extract`` returns None on failure (the caller
    then falls back to the deterministic extractor); ``describe`` reports
    provenance metadata recorded in structure.json."""

    def extract(self, utterances: list[Utterance]) -> DiscourseExtraction | None: ...

    def describe(self) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Statement splitting
# ---------------------------------------------------------------------------

_SENTENCE_ENDERS = "。！？!?\n"
# Strong connectives that open a new discourse move mid-sentence when they
# follow a comma ("…だ、なぜなら…"). Kept short and high-precision.
_SPLIT_CONNECTIVES = (
    "なぜなら",
    "というのも",
    "そのため",
    "だから",
    "したがって",
    "つまり",
    "要するに",
    "しかし",
    "一方で",
    "ただし",
)


def _split_sentences(text: str) -> list[str]:
    pieces: list[str] = []
    current: list[str] = []
    for char in text:
        current.append(char)
        if char in _SENTENCE_ENDERS:
            piece = "".join(current).strip()
            if piece.strip("".join(_SENTENCE_ENDERS)):
                pieces.append(piece)
            current = []
    tail = "".join(current).strip()
    if tail:
        pieces.append(tail)
    return pieces


def _split_on_connectives(sentence: str) -> list[str]:
    """Split before a strong connective that follows a touten (、)."""
    parts: list[str] = []
    remaining = sentence
    while True:
        best: int | None = None
        for marker in _SPLIT_CONNECTIVES:
            probe = remaining.find("、" + marker)
            if probe != -1 and (best is None or probe < best):
                best = probe
        if best is None:
            break
        head = remaining[: best + 1].strip()
        if head.strip("、"):
            parts.append(head)
        remaining = remaining[best + 1 :]
    if remaining.strip():
        parts.append(remaining.strip())
    return parts or [sentence]


def split_statements(utterances: list[Utterance]) -> list[Statement]:
    """Deterministic statement segmentation with ids s1..sN in time order."""
    statements: list[Statement] = []
    counter = 0
    for utterance in utterances:
        for sentence in _split_sentences(utterance.text):
            for fragment in _split_on_connectives(sentence):
                counter += 1
                statements.append(
                    Statement(
                        id=f"s{counter}",
                        utterance_index=utterance.index,
                        speaker=utterance.speaker,
                        text=fragment,
                    )
                )
    return statements


# ---------------------------------------------------------------------------
# Fallback (deterministic, marker-based) relation extraction
# ---------------------------------------------------------------------------

# marker -> (relation type, direction, confidence, rule name)
# direction "cur->prev": source = current statement, target = previous.
# direction "prev->cur": source = previous statement, target = current.
_MARKER_RULES: tuple[tuple[str, str, str, float, str], ...] = (
    ("なぜなら", "supports", "cur->prev", 0.9, "explicit_reason"),
    ("というのも", "supports", "cur->prev", 0.9, "explicit_reason"),
    ("そのため", "causes", "prev->cur", 0.9, "explicit_causal"),
    ("だから", "causes", "prev->cur", 0.9, "explicit_causal"),
    ("したがって", "causes", "prev->cur", 0.9, "explicit_causal"),
    ("その結果", "causes", "prev->cur", 0.9, "explicit_causal"),
    ("つまり", "elaborates", "prev->cur", 0.75, "elaboration"),
    ("要するに", "elaborates", "prev->cur", 0.75, "elaboration"),
    ("すなわち", "elaborates", "prev->cur", 0.75, "elaboration"),
    ("例えば", "elaborates", "prev->cur", 0.75, "elaboration"),
    ("具体的には", "elaborates", "prev->cur", 0.75, "elaboration"),
    ("しかし", "contrasts", "cur->prev", 0.65, "contrast"),
    ("一方で", "contrasts", "cur->prev", 0.65, "contrast"),
    ("ただし", "contrasts", "cur->prev", 0.65, "contrast"),
    ("とはいえ", "contrasts", "cur->prev", 0.65, "contrast"),
    # でも/けど are conversationally overloaded (low precision) -> weak.
    ("でも", "contrasts", "cur->prev", 0.45, "weak_contrast"),
    ("だけど", "contrasts", "cur->prev", 0.45, "weak_contrast"),
)

_TURN_INITIAL_CONTRAST_CONFIDENCE = 0.65
_WEAK_CONTRAST_CONFIDENCE = 0.45


class FallbackRelationExtractor:
    """Deterministic connective-marker extractor.

    Used when the Claude extractor is unavailable (no ANTHROPIC_API_KEY, SDK
    missing) or fails. High precision / low recall by design; every relation
    carries evidence {marker, rule} so the UI can show why an arrow exists.
    Topics are left empty — the caller clusters them via cluster_topics().
    """

    def describe(self) -> dict[str, Any]:
        return {"name": "fallback", "model": None, "effort": None}

    def extract(self, utterances: list[Utterance]) -> DiscourseExtraction | None:
        statements = split_statements(utterances)
        relations: list[Relation] = []
        for position, statement in enumerate(statements):
            if position == 0:
                continue
            previous = statements[position - 1]
            rule = self._match_marker(statement.text)
            if rule is None:
                continue
            marker, rel_type, direction, confidence, rule_name = rule
            if rel_type == "contrasts":
                confidence, rule_name = self._contrast_strength(
                    statement, previous, confidence, rule_name
                )
            source, target = (
                (statement.id, previous.id)
                if direction == "cur->prev"
                else (previous.id, statement.id)
            )
            relations.append(
                Relation(
                    source=source,
                    target=target,
                    type=rel_type,
                    confidence=confidence,
                    evidence={"marker": marker, "rule": rule_name},
                )
            )
        return DiscourseExtraction(
            statements=tuple(statements), relations=tuple(relations), topics=()
        )

    @staticmethod
    def _match_marker(text: str) -> tuple[str, str, str, float, str] | None:
        stripped = text.lstrip("、。 　")
        for marker, rel_type, direction, confidence, rule in _MARKER_RULES:
            if stripped.startswith(marker):
                return marker, rel_type, direction, confidence, rule
        return None

    @staticmethod
    def _contrast_strength(
        statement: Statement, previous: Statement, confidence: float, rule: str
    ) -> tuple[float, str]:
        """Turn-initial contrast by another speaker likely targets the prior
        speaker's point (0.65); same-turn contrast is weaker (0.45)."""
        turn_initial = (
            statement.utterance_index != previous.utterance_index
            and statement.speaker != previous.speaker
        )
        if turn_initial:
            return max(confidence, _TURN_INITIAL_CONTRAST_CONFIDENCE), rule
        if rule == "weak_contrast":
            return _WEAK_CONTRAST_CONFIDENCE, rule
        return _WEAK_CONTRAST_CONFIDENCE, "same_turn_contrast"


# ---------------------------------------------------------------------------
# Topic clustering (fallback path; Claude path returns topics itself)
# ---------------------------------------------------------------------------


def _statement_terms(statements: list[Statement]) -> list[Statement]:
    """Attach content terms to statements (janome-or-legacy via terms.py).

    Statements that already carry terms (e.g. from an injected extractor in
    tests) are kept as-is, which also makes clustering testable without any
    tokenizer dependency.
    """
    from .live.terms import extract_terms  # import-safe without janome

    enriched: list[Statement] = []
    for statement in statements:
        if statement.terms:
            enriched.append(statement)
            continue
        terms = tuple(
            t.word for t in extract_terms(statement.text, _TOPIC_TERMS_PER_STATEMENT)
        )
        enriched.append(replace(statement, terms=terms))
    return enriched


def cluster_topics(statements: list[Statement]) -> tuple[list[Statement], list[Topic]]:
    """Deterministic topic clustering over statement-term co-occurrence.

    1. Terms co-occurring in one statement gain edge weight.
    2. Weak edges (< _TOPIC_MIN_EDGE_WEIGHT) are pruned; connected components
       become topic candidates. Oversized components are refined with a
       bounded, deterministic label propagation.
    3. Statements join the topic with the highest member-term weight sum.
    Returns statements (with terms + topic_id filled) and topics. No sklearn,
    no embeddings, no randomness.
    """
    statements = _statement_terms(list(statements))
    term_weight: dict[str, int] = {}
    edge_weight: dict[tuple[str, str], int] = {}
    for statement in statements:
        unique_terms = sorted(set(statement.terms))
        for term in unique_terms:
            term_weight[term] = term_weight.get(term, 0) + 1
        for i in range(len(unique_terms)):
            for j in range(i + 1, len(unique_terms)):
                key = (unique_terms[i], unique_terms[j])
                edge_weight[key] = edge_weight.get(key, 0) + 1

    kept_edges = {
        key: weight
        for key, weight in edge_weight.items()
        if weight >= _TOPIC_MIN_EDGE_WEIGHT
    }
    neighbors: dict[str, dict[str, int]] = {term: {} for term in term_weight}
    for (a, b), weight in kept_edges.items():
        neighbors[a][b] = weight
        neighbors[b][a] = weight

    components = _connected_components(sorted(term_weight), neighbors)
    clusters: list[list[str]] = []
    for component in components:
        if len(component) > _TOPIC_MAX_COMPONENT:
            clusters.extend(_label_propagation(component, neighbors))
        else:
            clusters.append(component)

    # Drop singleton clusters of weight-1 terms (noise), keep everything else.
    clusters = [
        cluster
        for cluster in clusters
        if len(cluster) > 1 or term_weight.get(cluster[0], 0) >= 2
    ]
    # Deterministic ordering: by total weight desc, then label.
    def cluster_key(cluster: list[str]) -> tuple[int, str]:
        return (-sum(term_weight[t] for t in cluster), min(cluster))

    clusters.sort(key=cluster_key)

    topics: list[Topic] = []
    term_to_topic: dict[str, str] = {}
    for number, cluster in enumerate(clusters, start=1):
        topic_id = f"t{number}"
        label = max(cluster, key=lambda t: (term_weight[t], t))
        topics.append(Topic(id=topic_id, label=label, statement_ids=()))
        for term in cluster:
            term_to_topic[term] = topic_id

    membership: dict[str, list[str]] = {topic.id: [] for topic in topics}
    assigned: list[Statement] = []
    for statement in statements:
        weights: dict[str, int] = {}
        for term in statement.terms:
            topic_id = term_to_topic.get(term)
            if topic_id is not None:
                weights[topic_id] = weights.get(topic_id, 0) + term_weight[term]
        topic_id = None
        if weights:
            topic_id = min(weights, key=lambda tid: (-weights[tid], tid))
            membership[topic_id].append(statement.id)
        assigned.append(replace(statement, topic_id=topic_id))

    final_topics = [
        replace(topic, statement_ids=tuple(membership[topic.id]))
        for topic in topics
        if membership[topic.id]
    ]
    return assigned, final_topics


def _connected_components(
    nodes: list[str], neighbors: dict[str, dict[str, int]]
) -> list[list[str]]:
    seen: set[str] = set()
    components: list[list[str]] = []
    for node in nodes:
        if node in seen:
            continue
        stack = [node]
        component: list[str] = []
        seen.add(node)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in sorted(neighbors.get(current, {})):
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(sorted(component))
    return components


def _label_propagation(
    component: list[str], neighbors: dict[str, dict[str, int]]
) -> list[list[str]]:
    """Deterministic weighted label propagation (fixed pass count, sorted
    iteration order, ties broken by smallest label)."""
    labels = {node: index for index, node in enumerate(sorted(component))}
    for _ in range(_TOPIC_MAX_LABEL_PASSES):
        changed = False
        for node in sorted(component):
            weight_by_label: dict[int, int] = {}
            for neighbor, weight in neighbors.get(node, {}).items():
                if neighbor in labels:
                    label = labels[neighbor]
                    weight_by_label[label] = weight_by_label.get(label, 0) + weight
            if not weight_by_label:
                continue
            best = min(weight_by_label, key=lambda lb: (-weight_by_label[lb], lb))
            if best != labels[node]:
                labels[node] = best
                changed = True
        if not changed:
            break
    groups: dict[int, list[str]] = {}
    for node, label in labels.items():
        groups.setdefault(label, []).append(node)
    return [sorted(group) for label, group in sorted(groups.items())]


# ---------------------------------------------------------------------------
# Local validation + DAG assembly
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return "".join(text.split())


def validate_extraction(
    extraction: DiscourseExtraction, utterances: list[Utterance]
) -> DiscourseExtraction:
    """Fail-soft validation of (possibly LLM-produced) output.

    Drops statements whose utterance reference is invalid or whose text is not
    loosely contained in the referenced utterance, relations with unknown
    endpoints/types, and topic member ids that don't exist. Confidence is
    clamped to [0, 1]. Deterministic; never raises on structural noise.
    """
    by_index = {u.index: u for u in utterances}
    kept_statements: list[Statement] = []
    for statement in extraction.statements:
        utterance = by_index.get(statement.utterance_index)
        if utterance is None or not statement.text.strip():
            continue
        if _normalize(statement.text) not in _normalize(utterance.text):
            continue
        speaker = statement.speaker or utterance.speaker
        kept_statements.append(replace(statement, speaker=speaker))

    ids = {s.id for s in kept_statements}
    kept_relations: list[Relation] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    for relation in extraction.relations:
        if relation.type not in RELATION_TYPES:
            continue
        if relation.source not in ids or relation.target not in ids:
            continue
        if relation.source == relation.target:
            continue
        pair = (relation.source, relation.target, relation.type)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        confidence = min(1.0, max(0.0, float(relation.confidence)))
        kept_relations.append(replace(relation, confidence=confidence))

    kept_topics: list[Topic] = []
    for topic in extraction.topics:
        member_ids = tuple(sid for sid in topic.statement_ids if sid in ids)
        if member_ids and topic.label:
            kept_topics.append(replace(topic, statement_ids=member_ids))

    return DiscourseExtraction(
        statements=tuple(kept_statements),
        relations=tuple(kept_relations),
        topics=tuple(kept_topics),
    )


def break_cycles(relations: list[Relation]) -> list[Relation]:
    """Deterministically drop the lowest-confidence edge of each cycle until
    the relation graph is a DAG (ties: larger (source, target) drops first)."""
    remaining = list(relations)
    while True:
        cycle = _find_cycle(remaining)
        if cycle is None:
            return remaining
        victim = min(cycle, key=lambda r: (r.confidence, r.source, r.target))
        # min() with (confidence, source, target): among equal confidences the
        # lexicographically smallest (source, target) is removed — stable.
        remaining.remove(victim)


def _find_cycle(relations: list[Relation]) -> list[Relation] | None:
    outgoing: dict[str, list[Relation]] = {}
    for relation in sorted(relations, key=lambda r: (r.source, r.target, r.type)):
        outgoing.setdefault(relation.source, []).append(relation)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    path: list[Relation] = []

    def visit(node: str) -> list[Relation] | None:
        color[node] = GRAY
        for relation in outgoing.get(node, []):
            state = color.get(relation.target, WHITE)
            if state == GRAY:
                # back edge -> cycle = path suffix from target + this edge
                start = next(
                    i for i, r in enumerate(path) if r.source == relation.target
                )
                return path[start:] + [relation]
            if state == WHITE:
                path.append(relation)
                found = visit(relation.target)
                if found is not None:
                    return found
                path.pop()
        color[node] = BLACK
        return None

    for node in sorted({r.source for r in relations}):
        if color.get(node, WHITE) == WHITE:
            found = visit(node)
            if found is not None:
                return found
    return None


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------


def build_structure(
    utterances: Iterable[Utterance | dict[str, Any]],
    extractor: RelationExtractor,
) -> dict[str, Any]:
    """Run ``extractor`` (falling back to FallbackRelationExtractor when it
    returns None), validate, cluster topics if absent, break cycles, and
    return the structure.json v1 payload (envelope fields added by caller)."""
    normalized = [_as_utterance(index, u) for index, u in enumerate(utterances)]

    used = extractor
    extraction = extractor.extract(normalized)
    if extraction is None:
        used = FallbackRelationExtractor()
        extraction = used.extract(normalized)
    assert extraction is not None  # fallback never returns None

    extraction = validate_extraction(extraction, normalized)
    statements = list(extraction.statements)
    topics = list(extraction.topics)
    if not topics:
        statements, topics = cluster_topics(statements)
    relations = break_cycles(list(extraction.relations))

    return {
        "kind": STRUCTURE_KIND,
        "utterances": [
            {
                "index": u.index,
                "speaker": u.speaker,
                "start": u.start,
                "end": u.end,
                "text": u.text,
            }
            for u in normalized
        ],
        "statements": [
            {
                "id": s.id,
                "utterance_index": s.utterance_index,
                "speaker": s.speaker,
                "text": s.text,
                "terms": list(s.terms),
                "topic_id": s.topic_id,
            }
            for s in statements
        ],
        "relations": [
            {
                "source": r.source,
                "target": r.target,
                "type": r.type,
                "confidence": r.confidence,
                "evidence": dict(r.evidence),
            }
            for r in relations
        ],
        "topics": [
            {
                "id": t.id,
                "label": t.label,
                "statement_ids": list(t.statement_ids),
            }
            for t in topics
        ],
        "extractors": [used.describe()],
    }


def _as_utterance(index: int, value: Utterance | dict[str, Any] | Any) -> Utterance:
    """Accept Utterance, mapping, or segment-like objects (TranscriptSegment)."""
    if isinstance(value, Utterance):
        return replace(value, index=index)
    if isinstance(value, dict):
        return Utterance(
            index=index,
            speaker=str(value.get("speaker", "")),
            start=float(value.get("start", 0.0)),
            end=float(value.get("end", 0.0)),
            text=str(value.get("text", "")),
        )
    return Utterance(
        index=index,
        speaker=str(getattr(value, "speaker", "")),
        start=float(getattr(value, "start", 0.0)),
        end=float(getattr(value, "end", 0.0)),
        text=str(getattr(value, "text", value)),
    )
