"""Unit tests for src/artifacts.py (batch keyword + word-network artifacts).

``extract_terms`` is patched with a deterministic fake (one Term per token) so
the tests do not depend on janome; save/load are exercised against a temp dir.
"""
import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import artifacts  # noqa: E402
from src.live.terms import Term  # noqa: E402


@contextmanager
def _fake_extract(fn):
    saved = artifacts.extract_terms
    artifacts.extract_terms = fn
    try:
        yield
    finally:
        artifacts.extract_terms = saved


def _by_words(text, limit):
    """Deterministic stand-in: one Term per whitespace token, score desc."""
    words = [w for w in text.replace("\n", " ").split(" ") if w]
    return [Term(w, float(len(words) - i)) for i, w in enumerate(words)][:limit]


def test_build_keywords_shape():
    with _fake_extract(_by_words):
        payload = artifacts.build_keywords(["alpha beta", "beta"], limit=10)
    assert "keywords" in payload
    assert all("word" in k and "score" in k for k in payload["keywords"])
    assert payload["keywords"][0]["word"] == "alpha"


def test_build_graph_node_ids_are_strings_and_json_safe():
    # Regression guard: nodes/edges must key on word strings, never Term
    # objects (Term is unorderable in _edge_key and not JSON-serializable).
    with _fake_extract(_by_words):
        payload = artifacts.build_graph(
            ["alpha beta", "beta gamma"], max_nodes=40, candidates_per_utterance=6
        )
    graph = payload["graph"]
    ids = {n["id"] for n in graph["nodes"]}
    assert ids == {"alpha", "beta", "gamma"}
    assert all(isinstance(n["id"], str) for n in graph["nodes"])
    beta = next(n for n in graph["nodes"] if n["id"] == "beta")
    assert beta["weight"] == 2  # appears in both utterances
    json.dumps(payload)  # must not raise


def test_build_graph_skips_empty_utterances():
    with _fake_extract(_by_words):
        payload = artifacts.build_graph(
            ["", "alpha beta", ""], max_nodes=40, candidates_per_utterance=6
        )
    assert {n["id"] for n in payload["graph"]["nodes"]} == {"alpha", "beta"}


def test_save_and_load_roundtrip():
    with _fake_extract(_by_words):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            artifacts.save_artifacts(out, "meeting.wav", ["alpha beta", "beta gamma"])
            assert (out / "meeting.keywords.json").exists()
            assert (out / "meeting.graph.json").exists()
            kw = artifacts.load_keywords(out, "meeting")
            gr = artifacts.load_graph(out, "meeting")
    assert kw is not None and "keywords" in kw
    assert kw["version"] == artifacts.SCHEMA_VERSION
    assert kw["source"] == "meeting.wav"
    assert gr is not None and "graph" in gr
    assert {n["id"] for n in gr["graph"]["nodes"]} == {"alpha", "beta", "gamma"}


def test_load_missing_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        assert artifacts.load_keywords(out, "nope") is None
        assert artifacts.load_graph(out, "nope") is None


def test_load_corrupt_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        (out / "bad.keywords.json").write_text("{not valid json", encoding="utf-8")
        assert artifacts.load_keywords(out, "bad") is None


def test_load_version_mismatch_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        (out / "old.graph.json").write_text(
            json.dumps({"version": 999, "graph": {"nodes": [], "edges": []}}),
            encoding="utf-8",
        )
        assert artifacts.load_graph(out, "old") is None


def test_load_wrong_payload_key_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        (out / "x.keywords.json").write_text(
            json.dumps({"version": artifacts.SCHEMA_VERSION}), encoding="utf-8"
        )
        assert artifacts.load_keywords(out, "x") is None


def _graph_envelope():
    return {
        "version": 1,
        "graph": {
            "type": "graph", "seq": 4,
            "nodes": [{"id": "alpha", "weight": 2}, {"id": "beta", "weight": 1}],
            "edges": [{"a": "alpha", "b": "beta", "weight": 2}],
        },
    }


def test_legacy_graph_gets_empty_edit_overlay():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "meeting.graph.json"
        path.write_text(json.dumps(_graph_envelope()), encoding="utf-8")
        graph = artifacts.load_graph(Path(tmp), "meeting")
    assert graph is not None
    assert graph["edits"] == artifacts.empty_graph_edits()


def test_graph_edits_roundtrip_canonical_and_preserves_base():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        original = _graph_envelope()
        (out / "meeting.graph.json").write_text(json.dumps(original), encoding="utf-8")
        saved = artifacts.update_graph_edits(out, "meeting", 0, {
            "nodes": [{"id": "extra"}],
            "edges": [{"a": "extra", "b": "alpha"}],
            "hidden_node_ids": [], "hidden_edges": [],
            "positions": [{"id": "extra", "x": 0.2, "y": 0.8}],
        })
        loaded = artifacts.load_graph(out, "meeting")
    assert saved["revision"] == 1
    assert saved["edges"] == [{"a": "alpha", "b": "extra"}]
    assert loaded is not None and loaded["graph"] == original["graph"]
    assert loaded["edits"] == saved


def test_graph_edits_reject_invalid_references_limits_and_coordinates():
    graph = _graph_envelope()["graph"]
    invalid = [
        {"nodes": [], "edges": [{"a": "alpha", "b": "missing"}], "hidden_node_ids": [], "hidden_edges": [], "positions": []},
        {"nodes": [], "edges": [], "hidden_node_ids": ["missing"], "hidden_edges": [], "positions": []},
        {"nodes": [], "edges": [], "hidden_node_ids": [], "hidden_edges": [], "positions": [{"id": "alpha", "x": 1.1, "y": 0}]},
        {"nodes": [{"id": str(i)} for i in range(artifacts.MAX_EDIT_NODES + 1)], "edges": [], "hidden_node_ids": [], "hidden_edges": [], "positions": []},
    ]
    for edits in invalid:
        try:
            artifacts.normalize_graph_edits(edits, graph)
        except artifacts.GraphEditsError:
            continue
        assert False, edits


def test_graph_edits_detect_revision_conflict_and_corruption_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        path = out / "meeting.graph.json"
        path.write_text(json.dumps(_graph_envelope()), encoding="utf-8")
        artifacts.update_graph_edits(out, "meeting", 0, {"nodes": [], "edges": [], "hidden_node_ids": [], "hidden_edges": [], "positions": []})
        try:
            artifacts.update_graph_edits(out, "meeting", 0, {"nodes": [], "edges": [], "hidden_node_ids": [], "hidden_edges": [], "positions": []})
        except artifacts.GraphRevisionConflict:
            pass
        else:
            assert False
        path.write_text("{ broken", encoding="utf-8")
        assert artifacts.load_graph(out, "meeting") is None


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
