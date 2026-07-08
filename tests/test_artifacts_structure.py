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
from src.discourse import FallbackRelationExtractor  # noqa: E402


class Seg:
    def __init__(self, speaker, text, start=0.0, end=1.0):
        self.speaker = speaker
        self.start = start
        self.end = end
        self.text = text


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
