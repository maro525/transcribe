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


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
