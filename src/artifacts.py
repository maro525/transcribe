"""Batch-completion artifacts: keywords, word-network graph, and (new)
logical discourse structure.

Depends only on stdlib + ``src.config`` + ``src.live.terms`` +
``src.live.graph`` + ``src.discourse`` (never torch, fastapi, or anthropic at
import time), so it is import-safe in the web layer and unit-testable without
the batch model stack. Mirrors the live session's extraction path (keywords
over the joined text, per-utterance graph accumulation) over a finished list
of utterances instead of a streaming feed.

File layout (version:1 envelope):
    output/{stem}.keywords.json
        {"version": 1, "source": ..., "generated_at": ...,
         "keywords": [{"word": str, "score": float}]}
    output/{stem}.graph.json
        {"version": 1, "source": ..., "generated_at": ...,
         "graph": <CooccurrenceGraph.snapshot() unchanged>}
    output/{stem}.structure.json                        (new, best-effort)
        {"version": 1, "source": ..., "generated_at": ...,
         "kind": "logical_structure", "utterances": [...],
         "statements": [...], "relations": [...], "topics": [...],
         "extractors": [{"name", "model", "effort"}]}

keywords.json / graph.json are byte-compatible with the previous release
(they still consume utterance *text* only); structure.json is a new, separate
file — old jobs simply don't have it and the detail page shows "なし".

Loaders fail closed: missing file, unreadable file, corrupt JSON, version
mismatch or missing payload key all collapse to ``None``.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from . import config
from .discourse import RelationExtractor, Utterance, build_structure
from .live.graph import CooccurrenceGraph
from .live.terms import extract_terms

SCHEMA_VERSION = 1
STRUCTURE_KIND = "logical_structure"
MAX_EDIT_NODES = 100
MAX_EDIT_EDGES = 250
MAX_HIDDEN_ITEMS = 250
MAX_NODE_ID_LENGTH = 120
MAX_GRAPH_EDITS_REQUEST_BYTES = 64 * 1024
_GRAPH_EDIT_LOCK = Lock()


class GraphEditsError(ValueError):
    """An edit overlay is malformed or cannot be applied."""


class GraphRevisionConflict(GraphEditsError):
    """The submitted edit revision is no longer current."""


def graph_edits_body_too_large(content_length: str | None) -> bool:
    """Return whether a declared graph-edit JSON payload exceeds 64 KiB."""
    if content_length is None:
        return False
    try:
        return int(content_length) > MAX_GRAPH_EDITS_REQUEST_BYTES
    except ValueError:
        return True


async def read_limited_graph_edits_payload(chunks: Any) -> bytes:
    """Read an async byte stream without retaining more than 64 KiB."""
    result: list[bytes] = []
    total = 0
    async for chunk in chunks:
        if len(chunk) > MAX_GRAPH_EDITS_REQUEST_BYTES - total:
            raise ValueError("edit request is too large")
        result.append(chunk)
        total += len(chunk)
    return b"".join(result)




def build_keywords(utterance_texts: list[str], *, limit: int) -> dict[str, Any]:
    """Keyword payload over the whole transcript (one utterance per line)."""
    terms = extract_terms("\n".join(utterance_texts), limit=limit)
    return {"keywords": [{"word": t.word, "score": t.score} for t in terms]}


def build_graph(
    utterance_texts: list[str],
    *,
    max_nodes: int,
    candidates_per_utterance: int,
) -> dict[str, Any]:
    """Co-occurrence graph payload (one utterance = one add_utterance call)."""
    graph = CooccurrenceGraph(max_nodes=max_nodes)  # decay=1.0 (off) by default
    for text in utterance_texts:
        if not text:
            continue
        graph.add_utterance(extract_terms(text, limit=candidates_per_utterance))
    return {"graph": graph.snapshot()}


def build_structure_payload(
    segments: list[Any], *, extractor: RelationExtractor | None = None
) -> dict[str, Any]:
    """Discourse-structure payload over the full segment list (speaker/start/
    end/text). ``extractor`` is injectable for tests; the default picks Claude
    when ANTHROPIC_API_KEY + SDK are present, else the deterministic fallback
    (that selection lives in discourse_llm, imported lazily so this module
    stays import-safe without the anthropic package)."""
    if extractor is None:
        from .discourse_llm import select_discourse_extractor

        extractor = select_discourse_extractor()
    return build_structure(_as_utterances(segments), extractor)


def save_artifacts(
    output_dir: Path,
    audio_filename: str,
    segments: list[Any],
    *,
    structure_extractor: RelationExtractor | None = None,
) -> None:
    """Write the artifact JSON files next to the transcript.

    ``segments`` may be TranscriptSegment-likes (speaker/start/end/text),
    dicts, or plain strings (backward compatible). keywords/graph consume the
    text only — their output is unchanged from the previous release. The new
    structure.json is best-effort: any failure is logged and swallowed so the
    job never fails because of it.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(audio_filename).stem
    utterance_texts = [u.text for u in _as_utterances(segments)]
    keywords = _envelope(
        audio_filename,
        build_keywords(utterance_texts, limit=config.LIVE_KEYWORD_LIMIT),
    )
    graph = _envelope(
        audio_filename,
        build_graph(
            utterance_texts,
            max_nodes=config.LIVE_GRAPH_MAX_NODES,
            candidates_per_utterance=config.LIVE_GRAPH_CANDIDATES_PER_FINAL,
        ),
    )
    _write_json(output_dir / f"{stem}.keywords.json", keywords)
    _write_json(output_dir / f"{stem}.graph.json", graph)

    if not config.DISCOURSE_ENABLED:
        return
    try:  # best-effort — never fail the job over the structure artifact
        structure = _envelope(
            audio_filename,
            build_structure_payload(segments, extractor=structure_extractor),
        )
        _write_json(output_dir / f"{stem}.structure.json", structure)
    except Exception as error:
        print(f"Structure generation failed for {audio_filename}: {error}")


def load_keywords(output_dir: Path, stem: str) -> dict[str, Any] | None:
    data = _load(output_dir / f"{stem}.keywords.json")
    return data if data is not None and "keywords" in data else None


def load_graph(output_dir: Path, stem: str) -> dict[str, Any] | None:
    data = _load(output_dir / f"{stem}.graph.json")
    if data is None or not isinstance(data.get("graph"), dict):
        return None
    try:
        data["edits"] = normalize_graph_edits(data.get("edits"), data["graph"])
    except GraphEditsError:
        return None
    return data


def empty_graph_edits() -> dict[str, Any]:
    """Return the canonical no-op overlay used by legacy graph artifacts."""
    return {"revision": 0, "nodes": [], "edges": [], "hidden_node_ids": [], "hidden_edges": [], "positions": []}


def normalize_graph_edits(edits: Any, graph: dict[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize a user overlay without mutating ``graph``."""
    if edits is None:
        return empty_graph_edits()
    if not isinstance(edits, dict) or set(edits) - {"revision", "nodes", "edges", "hidden_node_ids", "hidden_edges", "positions"}:
        raise GraphEditsError("invalid edits object")
    revision = edits.get("revision", 0)
    if type(revision) is not int or revision < 0:
        raise GraphEditsError("invalid revision")
    base_ids = _graph_node_ids(graph)
    base_edges = {_edge_key(item) for item in graph.get("edges", []) if _is_edge(item)}
    nodes = _unique_ids(edits.get("nodes", []), "nodes", MAX_EDIT_NODES, item_key="id")
    node_ids = {item["id"] for item in nodes}
    if node_ids & base_ids:
        raise GraphEditsError("user node duplicates base node")
    hidden_node_ids = _unique_ids(edits.get("hidden_node_ids", []), "hidden_node_ids", MAX_HIDDEN_ITEMS)
    hidden_ids = set(hidden_node_ids)
    if not hidden_ids <= base_ids:
        raise GraphEditsError("unknown hidden base node")
    visible_ids = (base_ids - hidden_ids) | node_ids
    edges = _unique_edges(edits.get("edges", []), "edges", MAX_EDIT_EDGES)
    if any(a not in visible_ids or b not in visible_ids for a, b in edges):
        raise GraphEditsError("edge endpoint is unavailable")
    if any(edge in base_edges for edge in edges):
        raise GraphEditsError("user edge duplicates base edge")
    hidden_edges = _unique_edges(edits.get("hidden_edges", []), "hidden_edges", MAX_HIDDEN_ITEMS)
    if any(edge not in base_edges for edge in hidden_edges):
        raise GraphEditsError("unknown hidden base edge")
    if any(a in hidden_ids or b in hidden_ids for a, b in hidden_edges):
        raise GraphEditsError("hidden edge has hidden endpoint")
    positions = _positions(edits.get("positions", []), visible_ids)
    return {
        "revision": revision,
        "nodes": nodes,
        "edges": [{"a": a, "b": b} for a, b in edges],
        "hidden_node_ids": sorted(hidden_ids),
        "hidden_edges": [{"a": a, "b": b} for a, b in hidden_edges],
        "positions": positions,
    }


def update_graph_edits(output_dir: Path, stem: str, expected_revision: int, edits: Any) -> dict[str, Any]:
    """Atomically replace a graph overlay when its revision still matches."""
    if type(expected_revision) is not int or expected_revision < 0:
        raise GraphEditsError("invalid expected revision")
    path = output_dir / f"{stem}.graph.json"
    with _GRAPH_EDIT_LOCK:
        envelope = _load(path)
        if envelope is None or not isinstance(envelope.get("graph"), dict):
            raise GraphEditsError("graph artifact is unavailable")
        current = normalize_graph_edits(envelope.get("edits"), envelope["graph"])
        if current["revision"] != expected_revision:
            raise GraphRevisionConflict("graph edits have changed")
        supplied = dict(edits) if isinstance(edits, dict) else edits
        if isinstance(supplied, dict):
            supplied["revision"] = current["revision"] + 1
        normalized = normalize_graph_edits(supplied, envelope["graph"])
        envelope["edits"] = normalized
        _atomic_write_json(path, envelope)
    return normalized


def load_structure(output_dir: Path, stem: str) -> dict[str, Any] | None:
    """Fail-closed like the other loaders + a ``kind`` check so an unrelated
    future file can never masquerade as a structure payload."""
    data = _load(output_dir / f"{stem}.structure.json")
    if data is None or data.get("kind") != STRUCTURE_KIND:
        return None
    if "statements" not in data or "relations" not in data:
        return None
    return data


def _as_utterances(segments: list[Any]) -> list[Utterance]:
    """Normalize strings / dicts / TranscriptSegment-likes to Utterance."""
    normalized: list[Utterance] = []
    for index, segment in enumerate(segments):
        if isinstance(segment, Utterance):
            item = Utterance(
                index=index,
                speaker=segment.speaker,
                start=segment.start,
                end=segment.end,
                text=segment.text,
            )
        elif isinstance(segment, str):
            item = Utterance(index=index, speaker="", start=0.0, end=0.0, text=segment)
        elif isinstance(segment, dict):
            item = Utterance(
                index=index,
                speaker=str(segment.get("speaker", "")),
                start=float(segment.get("start", 0.0)),
                end=float(segment.get("end", 0.0)),
                text=str(segment.get("text", "")),
            )
        else:
            item = Utterance(
                index=index,
                speaker=str(getattr(segment, "speaker", "")),
                start=float(getattr(segment, "start", 0.0)),
                end=float(getattr(segment, "end", 0.0)),
                text=str(getattr(segment, "text", "")),
            )
        normalized.append(item)
    return normalized


def _envelope(audio_filename: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "source": audio_filename,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        **payload,
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def _graph_node_ids(graph: dict[str, Any]) -> set[str]:
    nodes = graph.get("nodes", [])
    if not isinstance(nodes, list):
        raise GraphEditsError("invalid base graph nodes")
    ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            raise GraphEditsError("invalid base graph node")
        ids.add(node["id"])
    return ids


def _valid_id(value: Any) -> str:
    if not isinstance(value, str):
        raise GraphEditsError("id must be a string")
    value = value.strip()
    if not value or len(value) > MAX_NODE_ID_LENGTH:
        raise GraphEditsError("invalid id")
    return value


def _unique_ids(value: Any, field: str, limit: int, item_key: str | None = None) -> list[Any]:
    if not isinstance(value, list) or len(value) > limit:
        raise GraphEditsError(f"invalid {field}")
    result: list[Any] = []
    seen: set[str] = set()
    for item in value:
        raw = item.get(item_key) if item_key and isinstance(item, dict) else item
        identifier = _valid_id(raw)
        if identifier in seen:
            raise GraphEditsError(f"duplicate {field}")
        seen.add(identifier)
        result.append({"id": identifier} if item_key else identifier)
    return result


def _is_edge(item: Any) -> bool:
    return isinstance(item, dict) and isinstance(item.get("a"), str) and isinstance(item.get("b"), str)


def _edge_key(item: dict[str, Any]) -> tuple[str, str]:
    a, b = _valid_id(item.get("a")), _valid_id(item.get("b"))
    if a == b:
        raise GraphEditsError("self edge")
    return tuple(sorted((a, b)))


def _unique_edges(value: Any, field: str, limit: int) -> list[tuple[str, str]]:
    if not isinstance(value, list) or len(value) > limit:
        raise GraphEditsError(f"invalid {field}")
    result = [_edge_key(item) for item in value if isinstance(item, dict)]
    if len(result) != len(value) or len(set(result)) != len(result):
        raise GraphEditsError(f"duplicate or invalid {field}")
    return sorted(result)


def _positions(value: Any, available_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_EDIT_NODES + MAX_HIDDEN_ITEMS:
        raise GraphEditsError("invalid positions")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise GraphEditsError("invalid position")
        identifier = _valid_id(item.get("id"))
        x, y = item.get("x"), item.get("y")
        if identifier in seen or identifier not in available_ids:
            raise GraphEditsError("invalid position id")
        if type(x) not in (int, float) or type(y) not in (int, float) or not math.isfinite(x) or not math.isfinite(y) or not 0 <= x <= 1 or not 0 <= y <= 1:
            raise GraphEditsError("invalid position coordinate")
        seen.add(identifier)
        result.append({"id": identifier, "x": float(x), "y": float(y)})
    return sorted(result, key=lambda item: item["id"])


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Durably replace JSON in-place without exposing a partial artifact."""
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary_name = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        return None
    return data
