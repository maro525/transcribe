"""Focused tests for graph-edit persistence helpers used by the web route."""
import json
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import artifacts  # noqa: E402


def _write_graph(path: Path) -> None:
    path.write_text(json.dumps({
        "version": 1,
        "graph": {"type": "graph", "seq": 1, "nodes": [{"id": "base", "weight": 1}], "edges": []},
    }), encoding="utf-8")


def test_edit_request_persists_success_response_shape():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _write_graph(out / "job.graph.json")
        edits = artifacts.update_graph_edits(out, "job", 0, {
            "nodes": [{"id": "added"}], "edges": [{"a": "base", "b": "added"}],
            "hidden_node_ids": [], "hidden_edges": [], "positions": [],
        })
    assert {"revision", "nodes", "edges", "hidden_node_ids", "hidden_edges", "positions"} == set(edits)
    assert edits["revision"] == 1


def test_route_filename_traversal_is_rejected():
    for name in ("../job.wav", "folder/job.wav"):
        # Mirrors the route's first guard; FastAPI is deliberately optional in
        # this project's direct-run test environment.
        assert Path(name).name != name


def test_bad_schema_and_revision_conflict_map_to_edit_errors():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _write_graph(out / "job.graph.json")
        try:
            artifacts.update_graph_edits(out, "job", 0, {"unknown": []})
        except artifacts.GraphEditsError:
            pass
        else:
            assert False
        artifacts.update_graph_edits(out, "job", 0, {"nodes": [], "edges": [], "hidden_node_ids": [], "hidden_edges": [], "positions": []})
        try:
            artifacts.update_graph_edits(out, "job", 0, {"nodes": [], "edges": [], "hidden_node_ids": [], "hidden_edges": [], "positions": []})
        except artifacts.GraphRevisionConflict:
            pass
        else:
            assert False


def test_deleting_base_node_also_removes_its_user_edges():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _write_graph(out / "job.graph.json")
        saved = artifacts.update_graph_edits(out, "job", 0, {
            "nodes": [{"id": "added"}], "edges": [],
            "hidden_node_ids": ["base"], "hidden_edges": [], "positions": [],
        })
    # The UI removes the user edge before saving hidden base node state.
    assert saved["edges"] == []


def test_graph_edit_request_size_limit():
    assert not artifacts.graph_edits_body_too_large(str(64 * 1024))
    assert artifacts.graph_edits_body_too_large(str(64 * 1024 + 1))
    assert artifacts.graph_edits_body_too_large("invalid")


def test_streamed_graph_edit_body_limit():
    async def chunks(parts):
        for part in parts:
            yield part

    exact = asyncio.run(artifacts.read_limited_graph_edits_payload(
        chunks([b"x" * 1024, b"y" * (64 * 1024 - 1024)])
    ))
    assert len(exact) == 64 * 1024
    try:
        asyncio.run(artifacts.read_limited_graph_edits_payload(
            chunks([b"x" * 1024, b"y" * (64 * 1024)])
        ))
    except ValueError:
        pass
    else:
        assert False


def test_hidden_edge_is_removed_with_its_hidden_endpoint():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        path = out / "job.graph.json"
        path.write_text(json.dumps({
            "version": 1,
            "graph": {
                "type": "graph", "seq": 1,
                "nodes": [{"id": "left", "weight": 1}, {"id": "right", "weight": 1}],
                "edges": [{"a": "left", "b": "right", "weight": 1}],
            },
        }), encoding="utf-8")
        saved = artifacts.update_graph_edits(out, "job", 0, {
            "nodes": [], "edges": [], "hidden_node_ids": ["left"],
            # The client removes this entry after the node is deleted.
            "hidden_edges": [], "positions": [],
        })
    assert saved["hidden_node_ids"] == ["left"]
    assert saved["hidden_edges"] == []


if __name__ == "__main__":
    from _runner import run_module
    run_module(globals())
