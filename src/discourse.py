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

# Decision-flow layer enums (D1). Kept as enum sets because structured output
# cannot express numeric ranges; unknown values are dropped in validation.
OPTION_STATUS = ("selected", "rejected", "abandoned", "unresolved", "partial")
OUTCOME_STATUS = ("decided", "deferred", "open")
OUTCOME_KIND = ("single_option", "hybrid", "no_option", "unknown")
ARG_STANCE = ("pro", "con", "neutral")
FLOW_CONFIDENCE = ("high", "medium", "low")

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
    id: str = ""  # stable ref for decision_flows arguments; set at assembly


@dataclass(frozen=True)
class Topic:
    id: str
    label: str
    statement_ids: tuple[str, ...] = ()
    summary: str = ""  # 1-line "what was discussed" (LLM path; "" otherwise)


# --- Decision-flow layer (D1): derived per-topic interpretation of the
# canonical statements/relations. Every field references existing ids and is
# additive; a v1 payload simply omits ``decision_flows``. ------------------


@dataclass(frozen=True)
class Question:
    """The core question / agenda a topic must decide."""

    id: str
    summary: str
    statement_id: str | None = None


@dataclass(frozen=True)
class Option:
    """A competing idea / candidate answer to a question."""

    id: str
    label: str
    summary: str = ""
    statement_ids: tuple[str, ...] = ()
    introduced_by: str | None = None
    status: str = "unresolved"  # one of OPTION_STATUS


@dataclass(frozen=True)
class Argument:
    """A pro/con/neutral point about one option — the decision-layer view of a
    discourse relation; ``relation_ids`` back-links to relations when possible."""

    id: str
    statement_id: str
    option_id: str
    stance: str  # one of ARG_STANCE
    relation_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Outcome:
    """How a question converged (decided / deferred / open)."""

    status: str  # one of OUTCOME_STATUS
    kind: str = "unknown"  # one of OUTCOME_KIND
    summary: str = ""
    statement_id: str | None = None
    selected_option_ids: tuple[str, ...] = ()
    rationale_statement_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecisionFlow:
    """Per-topic flow: question -> competing options -> convergence. References
    canonical statement/relation/topic ids; never replaces them."""

    topic_id: str
    questions: tuple[Question, ...] = ()
    options: tuple[Option, ...] = ()
    arguments: tuple[Argument, ...] = ()
    outcome: Outcome | None = None
    confidence: str = "medium"  # one of FLOW_CONFIDENCE
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiscourseExtraction:
    """What a RelationExtractor returns (topics may be empty -> clustered
    locally by :func:`cluster_topics`)."""

    statements: tuple[Statement, ...]
    relations: tuple[Relation, ...]
    topics: tuple[Topic, ...] = ()
    decision_flows: tuple[DecisionFlow, ...] = ()


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
        # statement.text may be a summary (not a verbatim substring), so no
        # substring check — utterance_index anchors it to a real turn.
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

    # decision_flows are validated separately (validate_decision_flows) after
    # relation ids exist; pass them through untouched here.
    return DiscourseExtraction(
        statements=tuple(kept_statements),
        relations=tuple(kept_relations),
        topics=tuple(kept_topics),
        decision_flows=extraction.decision_flows,
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
# Decision-flow layer: id numbering + fail-soft validation (D2)
# ---------------------------------------------------------------------------


def _assign_relation_ids(relations: list[Relation]) -> list[Relation]:
    """Give every relation a stable ``id`` used by decision_flows arguments.

    LLM-provided ids are kept when non-empty and unique (so the model's own
    ``relation_ids`` references keep resolving); the rest get deterministic
    ``r{n}`` ids that avoid collisions. Deterministic in list order.
    """
    counts: dict[str, int] = {}
    for relation in relations:
        if relation.id:
            counts[relation.id] = counts.get(relation.id, 0) + 1
    keepable = {rid for rid, n in counts.items() if n == 1}
    used = set(keepable)
    counter = 0

    def next_id() -> str:
        nonlocal counter
        while True:
            counter += 1
            candidate = f"r{counter}"
            if candidate not in used:
                used.add(candidate)
                return candidate

    out: list[Relation] = []
    for relation in relations:
        if relation.id and relation.id in keepable:
            out.append(relation)
        else:
            out.append(replace(relation, id=next_id()))
    return out


def validate_decision_flows(
    flows: Iterable[DecisionFlow],
    statements: list[Statement],
    topics: list[Topic],
    relations: list[Relation],
) -> list[DecisionFlow]:
    """Fail-soft validation of the derived decision-flow layer (D2).

    A *hard* violation (bad reference, duplicate id, unknown enum, broken
    outcome cardinality) drops the **whole flow** — the base structure always
    survives. *Soft* inconsistencies are appended to that flow's ``warnings``
    and the flow is kept. Deterministic; never raises on noisy input.
    """
    topic_ids = {t.id for t in topics}
    stmt_topic: dict[str, str] = {}
    for topic in topics:
        for sid in topic.statement_ids:
            stmt_topic.setdefault(sid, topic.id)
    for statement in statements:
        if statement.topic_id and statement.id not in stmt_topic:
            stmt_topic[statement.id] = statement.topic_id
    stmt_ids = {s.id for s in statements}
    utt_index = {s.id: s.utterance_index for s in statements}
    rel_ids = {r.id for r in relations if r.id}
    rel_type = {r.id: r.type for r in relations if r.id}

    kept: list[DecisionFlow] = []
    for flow in flows:
        cleaned = _validate_one_flow(
            flow, topic_ids, stmt_topic, stmt_ids, utt_index, rel_ids, rel_type
        )
        if cleaned is not None:
            kept.append(cleaned)
    return kept


def _validate_one_flow(
    flow: DecisionFlow,
    topic_ids: set[str],
    stmt_topic: dict[str, str],
    stmt_ids: set[str],
    utt_index: dict[str, int],
    rel_ids: set[str],
    rel_type: dict[str, str],
) -> DecisionFlow | None:
    if flow.topic_id not in topic_ids:
        return None

    def in_topic(sid: str | None) -> bool:
        # a referenced statement must exist AND belong to this flow's topic
        return bool(sid) and sid in stmt_ids and stmt_topic.get(sid) == flow.topic_id

    # --- enum validity (hard: unknown -> drop, never render garbage) ---
    if flow.confidence not in FLOW_CONFIDENCE:
        return None
    if any(o.status not in OPTION_STATUS for o in flow.options):
        return None
    if any(a.stance not in ARG_STANCE for a in flow.arguments):
        return None
    if flow.outcome is not None and (
        flow.outcome.status not in OUTCOME_STATUS
        or flow.outcome.kind not in OUTCOME_KIND
    ):
        return None

    # --- entity ids must be present (blank ids would silently collide) ---
    if any(not (q.id or "").strip() for q in flow.questions):
        return None
    if any(not (o.id or "").strip() for o in flow.options):
        return None
    if any(not (a.id or "").strip() for a in flow.arguments):
        return None

    # --- id uniqueness within the flow (hard) ---
    q_ids = [q.id for q in flow.questions]
    o_ids = [o.id for o in flow.options]
    a_ids = [a.id for a in flow.arguments]
    for id_list in (q_ids, o_ids, a_ids):
        if len(id_list) != len(set(id_list)):
            return None
    option_ids = set(o_ids)

    # --- reference integrity (hard) ---
    for question in flow.questions:
        if question.statement_id is not None and not in_topic(question.statement_id):
            return None
    for option in flow.options:
        if not option.statement_ids:
            return None
        if not all(in_topic(sid) for sid in option.statement_ids):
            return None
        if (
            option.introduced_by is not None
            and option.introduced_by not in option.statement_ids
        ):
            return None
    for argument in flow.arguments:
        if argument.option_id not in option_ids or not in_topic(argument.statement_id):
            return None
    if flow.outcome is not None:
        outcome = flow.outcome
        if outcome.statement_id is not None and not in_topic(outcome.statement_id):
            return None
        if any(oid not in option_ids for oid in outcome.selected_option_ids):
            return None
        if any(not in_topic(sid) for sid in outcome.rationale_statement_ids):
            return None
        selected = len(outcome.selected_option_ids)
        if outcome.status == "decided" and outcome.kind == "single_option" and selected != 1:
            return None
        if outcome.status == "decided" and outcome.kind == "hybrid" and selected < 2:
            return None

    # --- soft checks -> warnings (keep the flow) ---
    warnings = list(flow.warnings)
    args_by_option: dict[str, list[Argument]] = {}
    for argument in flow.arguments:
        args_by_option.setdefault(argument.option_id, []).append(argument)
    for option in flow.options:
        if option.id not in args_by_option:
            warnings.append(f"option {option.id} has no argument")
    for argument in flow.arguments:
        if argument.relation_ids and not any(r in rel_ids for r in argument.relation_ids):
            warnings.append(f"argument {argument.id} references no known relation")
        if argument.stance == "con" and any(
            rel_type.get(r) == "supports" for r in argument.relation_ids
        ):
            warnings.append(f"con argument {argument.id} cites a supports relation")
    if flow.outcome is not None:
        for oid in flow.outcome.selected_option_ids:
            stances = {a.stance for a in args_by_option.get(oid, [])}
            if stances == {"con"}:
                warnings.append(f"selected option {oid} has only con arguments")
        if flow.outcome.statement_id in utt_index:
            outcome_t = utt_index[flow.outcome.statement_id]
            option_times = [
                utt_index[sid]
                for option in flow.options
                for sid in option.statement_ids
                if sid in utt_index
            ]
            if option_times and outcome_t < min(option_times):
                warnings.append("outcome precedes option discussion")

    return replace(flow, warnings=tuple(warnings))


def _serialize_decision_flows(flows: list[DecisionFlow]) -> list[dict[str, Any]]:
    """Wire form of the decision-flow layer (mirrors D1's JSON)."""
    def outcome_dict(outcome: Outcome | None) -> dict[str, Any] | None:
        if outcome is None:
            return None
        return {
            "status": outcome.status,
            "kind": outcome.kind,
            "summary": outcome.summary,
            "statement_id": outcome.statement_id,
            "selected_option_ids": list(outcome.selected_option_ids),
            "rationale_statement_ids": list(outcome.rationale_statement_ids),
        }

    return [
        {
            "topic_id": flow.topic_id,
            "questions": [
                {"id": q.id, "summary": q.summary, "statement_id": q.statement_id}
                for q in flow.questions
            ],
            "options": [
                {
                    "id": o.id,
                    "label": o.label,
                    "summary": o.summary,
                    "statement_ids": list(o.statement_ids),
                    "introduced_by": o.introduced_by,
                    "status": o.status,
                }
                for o in flow.options
            ],
            "arguments": [
                {
                    "id": a.id,
                    "statement_id": a.statement_id,
                    "option_id": a.option_id,
                    "stance": a.stance,
                    "relation_ids": list(a.relation_ids),
                }
                for a in flow.arguments
            ],
            "outcome": outcome_dict(flow.outcome),
            "confidence": flow.confidence,
            "warnings": list(flow.warnings),
        }
        for flow in flows
    ]


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------


def _assign_topic_ids(
    statements: list[Statement], topics: list[Topic]
) -> list[Statement]:
    """Fill each statement's ``topic_id`` from the topics' ``statement_ids``
    (LLM extractors report membership on topics, not on statements). First
    topic wins if a statement is listed under several."""
    by_statement: dict[str, str] = {}
    for topic in topics:
        for sid in topic.statement_ids:
            by_statement.setdefault(sid, topic.id)
    return [
        replace(s, topic_id=by_statement.get(s.id, s.topic_id)) for s in statements
    ]


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
    else:
        # LLM returns membership on topics; mirror it onto each statement so
        # the detail-page lanes/treemap can group by statement.topic_id.
        statements = _assign_topic_ids(statements, topics)
    relations = _assign_relation_ids(break_cycles(list(extraction.relations)))
    decision_flows = validate_decision_flows(
        list(extraction.decision_flows), statements, topics, relations
    )

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
                "id": r.id,
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
                "summary": t.summary,
                "statement_ids": list(t.statement_ids),
            }
            for t in topics
        ],
        "decision_flows": _serialize_decision_flows(decision_flows),
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
