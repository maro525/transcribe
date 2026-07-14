"""Unit tests for src/discourse.py — deterministic discourse-structure logic.

Statement splitting, fallback marker extraction, topic clustering, validation,
cycle-breaking, and payload assembly. No anthropic / janome / network.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.discourse import (  # noqa: E402
    Argument,
    DecisionFlow,
    DiscourseExtraction,
    FallbackRelationExtractor,
    Option,
    Outcome,
    Question,
    Relation,
    Statement,
    Topic,
    Utterance,
    break_cycles,
    build_structure,
    cluster_topics,
    split_statements,
    validate_decision_flows,
    validate_extraction,
)


def _utt(index, speaker, text, start=0.0, end=1.0):
    return Utterance(index=index, speaker=speaker, start=start, end=end, text=text)


class FakeExtractor:
    def __init__(self, extraction):
        self._extraction = extraction

    def extract(self, utterances):
        return self._extraction

    def describe(self):
        return {"name": "fake", "model": "test", "effort": None}


class NoneExtractor:
    def extract(self, utterances):
        return None

    def describe(self):
        return {"name": "none", "model": None, "effort": None}


def test_split_statements_ids_and_sentences():
    utts = [_utt(0, "A", "リリースします。なぜなら準備できたからです。")]
    statements = split_statements(utts)
    assert [s.id for s in statements] == [f"s{i + 1}" for i in range(len(statements))]
    assert any("リリースします" in s.text for s in statements)
    assert any(s.text.startswith("なぜなら") for s in statements)


def test_split_on_connective_after_touten():
    utts = [_utt(0, "A", "採用しよう、なぜなら速いからだ")]
    statements = split_statements(utts)
    assert len(statements) == 2
    assert statements[1].text.startswith("なぜなら")


def test_fallback_reason_direction():
    utts = [_utt(0, "A", "採用すべきだ。"), _utt(1, "A", "なぜなら速いからだ。")]
    ext = FallbackRelationExtractor().extract(utts)
    supports = [r for r in ext.relations if r.type == "supports"]
    assert len(supports) == 1
    # supports: 根拠(なぜなら文=current) → 主張(previous)
    assert supports[0].source == "s2" and supports[0].target == "s1"
    assert supports[0].evidence["marker"] == "なぜなら"


def test_fallback_causal_direction():
    utts = [_utt(0, "A", "遅延した。"), _utt(1, "A", "そのため延期する。")]
    ext = FallbackRelationExtractor().extract(utts)
    causes = [r for r in ext.relations if r.type == "causes"]
    assert len(causes) == 1
    # causes: 原因(previous) → 結果(current)
    assert causes[0].source == "s1" and causes[0].target == "s2"


def test_fallback_is_deterministic():
    utts = [_utt(0, "A", "Xだ。"), _utt(1, "B", "しかしYだ。")]
    a = FallbackRelationExtractor().extract(utts)
    b = FallbackRelationExtractor().extract(utts)
    key = lambda ext: [(r.source, r.target, r.type, r.confidence) for r in ext.relations]
    assert key(a) == key(b)


def test_validate_keeps_summaries_drops_bad_refs():
    # statement.text may be a summary (not a substring) — only bad utterance
    # refs / empty text / bad relations / unknown topic members are dropped.
    utts = [_utt(0, "A", "実際の発話テキスト")]
    ext = DiscourseExtraction(
        statements=(
            Statement("s1", 0, "A", "実際の発話"),  # valid ref -> kept
            Statement("s2", 0, "A", "逐語でない要約"),  # summary -> now kept
            Statement("s3", 9, "A", "実際の"),  # bad utterance ref -> dropped
            Statement("s4", 0, "A", "   "),  # empty -> dropped
        ),
        relations=(
            Relation("s1", "s2", "elaborates", 0.8),  # valid -> kept
            Relation("s1", "s1", "causes", 0.9),  # self loop -> dropped
            Relation("s1", "s9", "supports", 0.5),  # unknown target -> dropped
            Relation("s1", "s2", "bogus", 0.8),  # bad type -> dropped
        ),
        topics=(Topic("t1", "話題", ("s1", "s99")),),
    )
    v = validate_extraction(ext, utts)
    assert {s.id for s in v.statements} == {"s1", "s2"}
    assert {(r.source, r.target, r.type) for r in v.relations} == {
        ("s1", "s2", "elaborates")
    }
    assert v.topics[0].statement_ids == ("s1",)


def test_build_structure_assigns_topic_ids_from_llm_topics():
    utts = [_utt(0, "A", "設計の話"), _utt(1, "B", "予算の話")]
    extraction = DiscourseExtraction(
        statements=(
            Statement("s1", 0, "A", "設計の要約"),
            Statement("s2", 1, "B", "予算の要約"),
        ),
        relations=(),
        topics=(Topic("t1", "設計", ("s1",)), Topic("t2", "予算", ("s2",))),
    )
    payload = build_structure(utts, FakeExtractor(extraction))
    by_id = {s["id"]: s for s in payload["statements"]}
    assert by_id["s1"]["topic_id"] == "t1"
    assert by_id["s2"]["topic_id"] == "t2"
    # every statement lands in a real topic (no その他 fallout)
    assert all(s["topic_id"] for s in payload["statements"])


def test_build_structure_topics_include_summary():
    utts = [_utt(0, "A", "設計の話"), _utt(1, "B", "予算の話")]
    extraction = DiscourseExtraction(
        statements=(
            Statement("s1", 0, "A", "設計の要約"),
            Statement("s2", 1, "B", "予算の要約"),
        ),
        relations=(),
        topics=(
            Topic("t1", "設計", ("s1",), summary="設計方針を検討した"),
            Topic("t2", "予算", ("s2",)),
        ),
    )
    payload = build_structure(utts, FakeExtractor(extraction))
    by_id = {t["id"]: t for t in payload["topics"]}
    assert by_id["t1"]["summary"] == "設計方針を検討した"
    assert by_id["t2"]["summary"] == ""  # default when omitted


def test_validate_clamps_confidence():
    utts = [_utt(0, "A", "aとbとc")]
    ext = DiscourseExtraction(
        statements=(Statement("s1", 0, "A", "aとb"), Statement("s2", 0, "A", "bとc")),
        relations=(Relation("s1", "s2", "causes", 5.0),),
    )
    v = validate_extraction(ext, utts)
    assert v.relations[0].confidence == 1.0


def test_break_cycles_produces_dag():
    rels = [
        Relation("s1", "s2", "causes", 0.9),
        Relation("s2", "s3", "causes", 0.8),
        Relation("s3", "s1", "causes", 0.3),  # lowest confidence -> dropped
    ]
    out = break_cycles(list(rels))
    assert len(out) == 2
    assert ("s3", "s1") not in {(r.source, r.target) for r in out}


def test_cluster_topics_deterministic():
    # statements carry terms directly -> no tokenizer dependency
    sts = [
        Statement("s1", 0, "A", "", terms=("api", "設計")),
        Statement("s2", 1, "A", "", terms=("api", "設計")),
        Statement("s3", 2, "B", "", terms=("予算", "採用")),
        Statement("s4", 3, "B", "", terms=("予算", "採用")),
    ]
    assigned1, topics1 = cluster_topics(list(sts))
    _assigned2, topics2 = cluster_topics(list(sts))
    assert [t.label for t in topics1] == [t.label for t in topics2]
    assert len(topics1) == 2
    assert all(s.topic_id is not None for s in assigned1)


def test_build_structure_shape_with_fake_extractor():
    utts = [_utt(0, "A", "設計を決める"), _utt(1, "B", "予算を確認する")]
    extraction = DiscourseExtraction(
        statements=(
            Statement("s1", 0, "A", "設計を決める"),
            Statement("s2", 1, "B", "予算を確認する"),
        ),
        relations=(Relation("s1", "s2", "elaborates", 0.7),),
        topics=(Topic("t1", "設計", ("s1", "s2")),),
    )
    payload = build_structure(utts, FakeExtractor(extraction))
    assert payload["kind"] == "logical_structure"
    assert {s["id"] for s in payload["statements"]} == {"s1", "s2"}
    assert payload["relations"][0]["type"] == "elaborates"
    assert payload["topics"][0]["label"] == "設計"
    assert payload["extractors"][0]["name"] == "fake"
    json.dumps(payload)  # must be serializable


def test_build_structure_falls_back_when_extractor_returns_none():
    utts = [_utt(0, "A", "採用する。"), _utt(1, "A", "なぜなら速いからだ。")]
    payload = build_structure(utts, NoneExtractor())
    assert payload["extractors"][0]["name"] == "fallback"
    assert any(r["type"] == "supports" for r in payload["relations"])


def test_build_structure_accepts_segment_like_objects():
    class Seg:
        def __init__(self, speaker, start, end, text):
            self.speaker = speaker
            self.start = start
            self.end = end
            self.text = text

    segs = [Seg("A", 0.0, 1.0, "採用する。"), Seg("A", 1.0, 2.0, "なぜなら速いからだ。")]
    payload = build_structure(segs, NoneExtractor())
    assert payload["utterances"][0]["speaker"] == "A"
    assert len(payload["statements"]) >= 2


# ---------------------------------------------------------------------------
# Decision-flow layer (NSKETCH-873)
# ---------------------------------------------------------------------------


def _flow_fixture(*, option_status_b="rejected", **flow_overrides):
    """A valid single-topic decision flow + its backing extraction/utterances.

    Returns (utterances, extraction). s1=question, s2/s3=options A/B,
    s4=pro-A argument, s5=outcome — all in topic t1."""
    utts = [_utt(i, "A", f"発話{i}") for i in range(5)]
    statements = (
        Statement("s1", 0, "A", "DBは何を使うか"),
        Statement("s2", 1, "A", "PostgreSQL案"),
        Statement("s3", 2, "B", "SQLite案"),
        Statement("s4", 3, "A", "運用実績がある"),
        Statement("s5", 4, "A", "PostgreSQLで進める"),
    )
    topics = (Topic("t1", "DB選定", ("s1", "s2", "s3", "s4", "s5")),)
    flow = DecisionFlow(
        topic_id="t1",
        questions=(Question("q1", "DBは何を使うか", "s1"),),
        options=(
            Option("o1", "PostgreSQL案", "", ("s2",), "s2", "selected"),
            Option("o2", "SQLite案", "", ("s3",), "s3", option_status_b),
        ),
        arguments=(Argument("a1", "s4", "o1", "pro"),),
        outcome=Outcome(
            "decided", "single_option", "PostgreSQLで進める", "s5", ("o1",), ("s4",)
        ),
        confidence="medium",
    )
    flow = _replace_flow(flow, **flow_overrides)
    extraction = DiscourseExtraction(
        statements=statements, relations=(), topics=topics, decision_flows=(flow,)
    )
    return utts, extraction


def _replace_flow(flow, **overrides):
    from dataclasses import replace

    return replace(flow, **overrides) if overrides else flow


def test_decision_flow_valid_passthrough_and_serialization():
    utts, extraction = _flow_fixture()
    payload = build_structure(utts, FakeExtractor(extraction))
    flows = payload["decision_flows"]
    assert len(flows) == 1
    flow = flows[0]
    assert flow["topic_id"] == "t1"
    assert [o["id"] for o in flow["options"]] == ["o1", "o2"]
    assert flow["outcome"]["status"] == "decided"
    assert flow["outcome"]["selected_option_ids"] == ["o1"]
    assert flow["confidence"] == "medium"
    # o2 has no argument -> soft warning, flow kept
    assert any("o2" in w for w in flow["warnings"])
    json.dumps(payload)  # serializable


def test_decision_flow_dropped_on_cross_topic_reference():
    utts, extraction = _flow_fixture()
    # add a second topic owning s3, but the option still lives in flow t1
    extraction = DiscourseExtraction(
        statements=extraction.statements,
        relations=extraction.relations,
        topics=(
            Topic("t1", "DB選定", ("s1", "s2", "s4", "s5")),
            Topic("t2", "別の話題", ("s3",)),
        ),
        decision_flows=extraction.decision_flows,
    )
    payload = build_structure(utts, FakeExtractor(extraction))
    # option o2 references s3 which now belongs to t2 -> whole flow dropped
    assert payload["decision_flows"] == []


def test_decision_flow_dropped_on_duplicate_ids():
    utts, extraction = _flow_fixture()
    flow = extraction.decision_flows[0]
    dup = _replace_flow(
        flow,
        options=(
            Option("o1", "A", "", ("s2",), "s2", "selected"),
            Option("o1", "B", "", ("s3",), "s3", "rejected"),  # duplicate id
        ),
    )
    extraction = DiscourseExtraction(
        statements=extraction.statements,
        relations=(),
        topics=extraction.topics,
        decision_flows=(dup,),
    )
    payload = build_structure(utts, FakeExtractor(extraction))
    assert payload["decision_flows"] == []


def test_decision_flow_dropped_on_bad_enum():
    utts, extraction = _flow_fixture()
    flow = extraction.decision_flows[0]
    bad = _replace_flow(
        flow, outcome=Outcome("bogus", "single_option", "", "s5", ("o1",), ())
    )
    extraction = DiscourseExtraction(
        statements=extraction.statements,
        relations=(),
        topics=extraction.topics,
        decision_flows=(bad,),
    )
    assert build_structure(utts, FakeExtractor(extraction))["decision_flows"] == []


def test_decision_flow_dropped_on_introduced_by_not_in_statements():
    utts, extraction = _flow_fixture()
    flow = extraction.decision_flows[0]
    bad = _replace_flow(
        flow,
        options=(
            Option("o1", "A", "", ("s2",), "s5", "selected"),  # introduced_by not in ids
            Option("o2", "B", "", ("s3",), "s3", "rejected"),
        ),
    )
    extraction = DiscourseExtraction(
        statements=extraction.statements,
        relations=(),
        topics=extraction.topics,
        decision_flows=(bad,),
    )
    assert build_structure(utts, FakeExtractor(extraction))["decision_flows"] == []


def test_decision_flow_single_option_cardinality_enforced():
    utts, extraction = _flow_fixture()
    flow = extraction.decision_flows[0]
    # decided + single_option but TWO selected -> dropped
    bad = _replace_flow(
        flow,
        outcome=Outcome("decided", "single_option", "", "s5", ("o1", "o2"), ()),
    )
    extraction = DiscourseExtraction(
        statements=extraction.statements,
        relations=(),
        topics=extraction.topics,
        decision_flows=(bad,),
    )
    assert build_structure(utts, FakeExtractor(extraction))["decision_flows"] == []


def test_relation_ids_assigned_and_deterministic():
    utts = [_utt(0, "A", "採用する。"), _utt(1, "A", "なぜなら速いからだ。")]
    extraction = DiscourseExtraction(
        statements=(
            Statement("s1", 0, "A", "採用する"),
            Statement("s2", 1, "A", "速い"),
        ),
        relations=(Relation("s2", "s1", "supports", 0.9),),
        topics=(Topic("t1", "採用", ("s1", "s2")),),
    )
    a = build_structure(utts, FakeExtractor(extraction))
    b = build_structure(utts, FakeExtractor(extraction))
    assert a["relations"][0]["id"] == "r1"
    assert [r["id"] for r in a["relations"]] == [r["id"] for r in b["relations"]]


def test_decision_flows_backward_compatible_when_absent():
    utts = [_utt(0, "A", "設計の話")]
    extraction = DiscourseExtraction(
        statements=(Statement("s1", 0, "A", "設計"),),
        relations=(),
        topics=(Topic("t1", "設計", ("s1",)),),
    )  # no decision_flows
    payload = build_structure(utts, FakeExtractor(extraction))
    assert payload["decision_flows"] == []


def test_validate_decision_flows_drops_unknown_topic():
    flow = DecisionFlow(topic_id="ghost", options=(Option("o1", "A", "", ("s1",), "s1"),))
    statements = [Statement("s1", 0, "A", "x", topic_id="t1")]
    topics = [Topic("t1", "T", ("s1",))]
    assert validate_decision_flows([flow], statements, topics, []) == []


def test_decision_flow_dropped_on_blank_entity_id():
    utts, extraction = _flow_fixture()
    flow = extraction.decision_flows[0]
    bad = _replace_flow(
        flow,
        options=(
            Option("", "A", "", ("s2",), "s2", "selected"),  # blank id -> drop flow
            Option("o2", "B", "", ("s3",), "s3", "rejected"),
        ),
    )
    extraction = DiscourseExtraction(
        statements=extraction.statements, relations=(), topics=extraction.topics,
        decision_flows=(bad,),
    )
    assert build_structure(utts, FakeExtractor(extraction))["decision_flows"] == []


def test_decision_flow_hybrid_cardinality():
    utts, extraction = _flow_fixture()
    flow = extraction.decision_flows[0]
    # decided + hybrid with TWO selected -> kept
    ok = _replace_flow(
        flow,
        options=(
            Option("o1", "A", "", ("s2",), "s2", "selected"),
            Option("o2", "B", "", ("s3",), "s3", "partial"),
        ),
        arguments=(Argument("a1", "s4", "o1", "pro"), Argument("a2", "s5", "o2", "pro")),
        outcome=Outcome("decided", "hybrid", "両案採用", "s5", ("o1", "o2"), ()),
    )
    kept = build_structure(
        utts,
        FakeExtractor(DiscourseExtraction(extraction.statements, (), extraction.topics, (ok,))),
    )["decision_flows"]
    assert len(kept) == 1 and kept[0]["outcome"]["kind"] == "hybrid"
    # ...but hybrid with only ONE selected -> dropped
    bad = _replace_flow(ok, outcome=Outcome("decided", "hybrid", "", "s5", ("o1",), ()))
    dropped = build_structure(
        utts,
        FakeExtractor(DiscourseExtraction(extraction.statements, (), extraction.topics, (bad,))),
    )["decision_flows"]
    assert dropped == []


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
