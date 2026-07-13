"""Unit tests for src/discourse.py — deterministic discourse-structure logic.

Statement splitting, fallback marker extraction, topic clustering, validation,
cycle-breaking, and payload assembly. No anthropic / janome / network.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.discourse import (  # noqa: E402
    DiscourseExtraction,
    FallbackRelationExtractor,
    Relation,
    Statement,
    Topic,
    Utterance,
    break_cycles,
    build_structure,
    cluster_topics,
    split_statements,
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


def test_validate_drops_bad_statements_relations_topics():
    utts = [_utt(0, "A", "実際の発話テキスト")]
    ext = DiscourseExtraction(
        statements=(
            Statement("s1", 0, "A", "実際の発話"),  # substring -> kept
            Statement("s2", 0, "A", "でっち上げ"),  # not substring -> dropped
            Statement("s3", 9, "A", "実際の"),  # bad utterance ref -> dropped
        ),
        relations=(
            Relation("s1", "s2", "supports", 0.9),  # endpoint dropped -> dropped
            Relation("s1", "s1", "causes", 0.9),  # self loop -> dropped
            Relation("s1", "s9", "supports", 0.5),  # unknown target -> dropped
        ),
        topics=(Topic("t1", "話題", ("s1", "s99")),),
    )
    v = validate_extraction(ext, utts)
    assert {s.id for s in v.statements} == {"s1"}
    assert v.relations == ()
    assert v.topics[0].statement_ids == ("s1",)


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


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
