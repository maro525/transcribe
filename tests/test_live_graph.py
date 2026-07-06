"""Unit tests for CooccurrenceGraph (live word-network aggregation)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.live.graph import CooccurrenceGraph  # noqa: E402


def _ids(snapshot: dict) -> set[str]:
    return {node["id"] for node in snapshot["nodes"]}


def test_add_utterance_creates_nodes_and_pairwise_edges():
    graph = CooccurrenceGraph(max_nodes=40)
    graph.add_utterance(["a", "b", "c"])
    snap = graph.snapshot()

    assert snap["type"] == "graph"
    assert snap["seq"] == 1
    assert _ids(snap) == {"a", "b", "c"}
    assert all(node["weight"] == 1 and node["last_seen"] == 1 for node in snap["nodes"])
    edge_pairs = {(edge["a"], edge["b"]) for edge in snap["edges"]}
    assert edge_pairs == {("a", "b"), ("a", "c"), ("b", "c")}
    assert all(edge["weight"] == 1 for edge in snap["edges"])


def test_repeated_cooccurrence_accumulates_weights():
    graph = CooccurrenceGraph(max_nodes=40)
    graph.add_utterance(["a", "b"])
    graph.add_utterance(["b", "a"])  # order must not matter for the edge key
    snap = graph.snapshot()

    assert snap["seq"] == 2
    weights = {node["id"]: node["weight"] for node in snap["nodes"]}
    assert weights == {"a": 2, "b": 2}
    assert len(snap["edges"]) == 1
    assert snap["edges"][0]["weight"] == 2


def test_duplicate_words_in_one_utterance_count_once_and_no_self_loop():
    graph = CooccurrenceGraph(max_nodes=40)
    graph.add_utterance(["a", "a", "b"])
    snap = graph.snapshot()

    weights = {node["id"]: node["weight"] for node in snap["nodes"]}
    assert weights == {"a": 1, "b": 1}
    assert len(snap["edges"]) == 1
    edge = snap["edges"][0]
    assert {edge["a"], edge["b"]} == {"a", "b"}


def test_prune_evicts_lowest_weight_and_orphan_edges():
    graph = CooccurrenceGraph(max_nodes=3)
    graph.add_utterance(["a", "b"])
    graph.add_utterance(["a", "b"])  # a/b weight 2
    graph.add_utterance(["c", "d"])  # 4 nodes -> prune down to 3
    snap = graph.snapshot()

    ids = _ids(snap)
    assert {"a", "b"} <= ids
    assert len(ids) == 3
    # every edge endpoint must still be a present node (no orphan edges)
    for edge in snap["edges"]:
        assert edge["a"] in ids
        assert edge["b"] in ids


def test_last_seen_tracks_latest_utterance():
    graph = CooccurrenceGraph(max_nodes=40)
    graph.add_utterance(["a", "b"])
    graph.add_utterance(["a", "c"])
    nodes = {node["id"]: node for node in graph.snapshot()["nodes"]}

    assert nodes["a"]["last_seen"] == 2
    assert nodes["b"]["last_seen"] == 1
    assert nodes["c"]["last_seen"] == 2


def test_prune_prefers_older_node_on_weight_tie():
    graph = CooccurrenceGraph(max_nodes=2)
    graph.add_utterance(["a"])
    graph.add_utterance(["b"])
    graph.add_utterance(["c"])  # all weight 1 -> "a" (oldest) is evicted

    assert _ids(graph.snapshot()) == {"b", "c"}


def test_reset_clears_everything():
    graph = CooccurrenceGraph(max_nodes=40)
    graph.add_utterance(["a", "b"])
    graph.reset()
    snap = graph.snapshot()

    assert snap["seq"] == 0
    assert snap["nodes"] == []
    assert snap["edges"] == []


def test_empty_and_blank_utterances_are_noops():
    graph = CooccurrenceGraph(max_nodes=40)
    graph.add_utterance([])
    graph.add_utterance(["", ""])
    snap = graph.snapshot()

    assert snap["seq"] == 0
    assert snap["nodes"] == []
    assert snap["edges"] == []


def test_single_word_utterance_adds_node_without_edges():
    graph = CooccurrenceGraph(max_nodes=40)
    graph.add_utterance(["a"])
    snap = graph.snapshot()

    assert _ids(snap) == {"a"}
    assert snap["edges"] == []


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
