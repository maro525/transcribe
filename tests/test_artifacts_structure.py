"""Structure-artifact integration tests.

save_artifacts writes structure.json best-effort (injected extractor keeps it
deterministic); load_structure is fail-closed on kind / payload-key / version.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import artifacts, config  # noqa: E402
from src.discourse import (  # noqa: E402
    Argument,
    DecisionFlow,
    DiscourseExtraction,
    FallbackRelationExtractor,
    Option,
    Outcome,
    Question,
    Statement,
    Topic,
)


class Seg:
    def __init__(self, speaker, text, start=0.0, end=1.0):
        self.speaker = speaker
        self.start = start
        self.end = end
        self.text = text


class _FlowExtractor:
    """Injected extractor returning a valid decision-flow-bearing extraction."""

    def describe(self):
        return {"name": "fake", "model": "test", "effort": None}

    def extract(self, utterances):
        return DiscourseExtraction(
            statements=(
                Statement("s1", 0, "A", "何を使うか"),
                Statement("s2", 1, "A", "案A"),
                Statement("s3", 2, "B", "案B"),
            ),
            relations=(),
            topics=(Topic("t1", "選定", ("s1", "s2", "s3")),),
            decision_flows=(
                DecisionFlow(
                    topic_id="t1",
                    questions=(Question("q1", "何を使うか", "s1"),),
                    options=(
                        Option("o1", "案A", "", ("s2",), "s2", "selected"),
                        Option("o2", "案B", "", ("s3",), "s3", "rejected"),
                    ),
                    arguments=(Argument("a1", "s2", "o1", "pro"),),
                    outcome=Outcome(
                        "decided", "single_option", "案Aで", "s2", ("o1",), ()
                    ),
                    confidence="medium",
                ),
            ),
        )


def test_save_artifacts_writes_structure_with_injected_extractor():
    segs = [Seg("A", "採用する。"), Seg("A", "なぜなら速いからだ。")]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        artifacts.save_artifacts(
            out, "meeting.wav", segs, structure_extractor=FallbackRelationExtractor()
        )
        assert (out / "meeting.structure.json").exists()
        loaded = artifacts.load_structure(out, "meeting")
    assert loaded is not None
    assert loaded["kind"] == "logical_structure"
    assert loaded["extractors"][0]["name"] == "fallback"
    assert any(r["type"] == "supports" for r in loaded["relations"])


def test_fallback_structure_has_no_decision_flows():
    # D4: the deterministic fallback never emits a decision layer.
    segs = [Seg("A", "採用する。"), Seg("A", "なぜなら速いからだ。")]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        artifacts.save_artifacts(
            out, "m.wav", segs, structure_extractor=FallbackRelationExtractor()
        )
        loaded = artifacts.load_structure(out, "m")
    assert loaded is not None
    assert loaded.get("decision_flows") == []


def test_decision_flows_round_trip():
    segs = [Seg("A", "何を使うか"), Seg("A", "案A"), Seg("B", "案B")]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        artifacts.save_artifacts(out, "m.wav", segs, structure_extractor=_FlowExtractor())
        loaded = artifacts.load_structure(out, "m")
    assert loaded is not None
    flows = loaded["decision_flows"]
    assert len(flows) == 1 and flows[0]["topic_id"] == "t1"
    assert flows[0]["outcome"]["selected_option_ids"] == ["o1"]


def test_save_artifacts_skips_structure_when_disabled():
    saved = config.DISCOURSE_ENABLED
    config.DISCOURSE_ENABLED = False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            artifacts.save_artifacts(
                out,
                "m.wav",
                [Seg("A", "リリース する")],
                structure_extractor=FallbackRelationExtractor(),
            )
            assert not (out / "m.structure.json").exists()
            assert (out / "m.keywords.json").exists()  # other artifacts unaffected
    finally:
        config.DISCOURSE_ENABLED = saved


def test_load_structure_fail_closed():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        assert artifacts.load_structure(out, "missing") is None
        (out / "wrongkind.structure.json").write_text(
            json.dumps(
                {"version": 1, "kind": "other", "statements": [], "relations": []}
            ),
            encoding="utf-8",
        )
        assert artifacts.load_structure(out, "wrongkind") is None
        (out / "nokeys.structure.json").write_text(
            json.dumps({"version": 1, "kind": "logical_structure"}), encoding="utf-8"
        )
        assert artifacts.load_structure(out, "nokeys") is None
        (out / "oldver.structure.json").write_text(
            json.dumps(
                {
                    "version": 999,
                    "kind": "logical_structure",
                    "statements": [],
                    "relations": [],
                }
            ),
            encoding="utf-8",
        )
        assert artifacts.load_structure(out, "oldver") is None


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
