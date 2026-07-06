"""Unit tests for CooccurrenceGraph (live word-network aggregation)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.live.graph import CooccurrenceGraph  # noqa: E402
from src.live.terms import Term  # noqa: E402


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


# --- cumulative-salience selection (Term input, decay, cutoffs) --------------


def test_term_scores_drive_selection_but_weight_stays_frequency():
    graph = CooccurrenceGraph(max_nodes=1)
    graph.add_utterance([Term("big", 5.0), Term("small", 1.0)])
    snap = graph.snapshot()

    assert _ids(snap) == {"big"}
    node = snap["nodes"][0]
    assert node["weight"] == 1  # frequency, not salience (wire format contract)
    assert isinstance(node["weight"], int)
    assert isinstance(node["last_seen"], int)


def test_hidden_candidate_keeps_accumulating_and_revives():
    graph = CooccurrenceGraph(max_nodes=2)
    graph.add_utterance([Term("a", 3.0), Term("b", 3.0), Term("c", 1.0)])
    assert _ids(graph.snapshot()) == {"a", "b"}  # c hidden but retained

    graph.add_utterance([Term("c", 2.0)])
    graph.add_utterance([Term("c", 2.0)])  # c cumulative salience 5.0 > 3.0
    assert "c" in _ids(graph.snapshot())


def test_decay_shrinks_salience_of_stale_words():
    graph = CooccurrenceGraph(max_nodes=1, decay=0.5)
    graph.add_utterance([Term("a", 1.0)])
    graph.add_utterance([Term("b", 1.0)])  # a decays to 0.5, b enters at 1.0
    assert _ids(graph.snapshot()) == {"b"}


def test_decay_applies_to_edge_weights_but_frequency_stays_int():
    graph = CooccurrenceGraph(max_nodes=40, decay=0.5)
    graph.add_utterance(["a", "b"])
    graph.add_utterance(["a", "b"])  # edge: 1 * 0.5 + 1 = 1.5
    snap = graph.snapshot()

    assert len(snap["edges"]) == 1
    assert abs(snap["edges"][0]["weight"] - 1.5) < 1e-9
    weights = {node["id"]: node["weight"] for node in snap["nodes"]}
    assert weights == {"a": 2, "b": 2}
    assert all(isinstance(node["weight"], int) for node in snap["nodes"])


def test_min_salience_cutoff_hides_until_accumulated():
    graph = CooccurrenceGraph(max_nodes=40, min_salience=1.5)
    graph.add_utterance([Term("a", 1.0)])
    assert graph.snapshot()["nodes"] == []

    graph.add_utterance([Term("a", 1.0)])  # cumulative 2.0 >= 1.5
    assert _ids(graph.snapshot()) == {"a"}


def test_min_frequency_cutoff_and_edge_projection():
    graph = CooccurrenceGraph(max_nodes=40, min_frequency=2)
    graph.add_utterance(["a", "b"])
    assert graph.snapshot()["nodes"] == []

    graph.add_utterance(["a"])  # a frequency 2 -> visible; b stays hidden
    snap = graph.snapshot()
    assert _ids(snap) == {"a"}
    assert snap["edges"] == []  # a-b edge hidden while b is not visible


def test_edges_projected_only_between_visible_nodes():
    graph = CooccurrenceGraph(max_nodes=2)
    graph.add_utterance([Term("a", 5.0), Term("b", 5.0), Term("c", 1.0)])
    snap = graph.snapshot()

    assert _ids(snap) == {"a", "b"}
    pairs = {(edge["a"], edge["b"]) for edge in snap["edges"]}
    assert pairs == {("a", "b")}


def test_max_candidates_evicts_lowest_salience_from_pool():
    graph = CooccurrenceGraph(max_nodes=2, max_candidates=2)
    graph.add_utterance([Term("a", 1.0)])
    graph.add_utterance([Term("b", 5.0)])
    graph.add_utterance([Term("c", 3.0)])  # pool over 2 -> "a" evicted for good

    assert set(graph._nodes) == {"b", "c"}
    assert _ids(graph.snapshot()) == {"b", "c"}


def test_no_decay_keeps_integer_edge_weights():
    graph = CooccurrenceGraph(max_nodes=40)  # decay 1.0 = off
    graph.add_utterance(["a", "b"])
    graph.add_utterance(["a", "b"])
    edge = graph.snapshot()["edges"][0]
    assert edge["weight"] == 2
    assert isinstance(edge["weight"], int)


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
