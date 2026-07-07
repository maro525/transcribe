"""Batch-completion artifacts: keyword list + word-network graph snapshots.

Depends only on stdlib + ``src.config`` + ``src.live.terms`` +
``src.live.graph`` (never torch or fastapi), so it is import-safe in the web
layer and unit-testable without the batch model stack. Mirrors the live
session's extraction path (keywords over the joined text, per-utterance graph
accumulation) over a finished list of utterance texts instead of a streaming
feed.

File layout (D3, version:1 envelope):
    output/{stem}.keywords.json
        {"version": 1, "source": ..., "generated_at": ...,
         "keywords": [{"word": str, "score": float}]}
    output/{stem}.graph.json
        {"version": 1, "source": ..., "generated_at": ...,
         "graph": <CooccurrenceGraph.snapshot() unchanged>}

Loaders fail closed: missing file, unreadable file, corrupt JSON, version
mismatch or missing payload key all collapse to ``None`` (the detail page
then shows "なし").
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config
from .live.graph import CooccurrenceGraph
from .live.terms import extract_terms

SCHEMA_VERSION = 1


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


def save_artifacts(
    output_dir: Path, audio_filename: str, utterance_texts: list[str]
) -> None:
    """Write both artifact JSON files next to the transcript."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(audio_filename).stem
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


def load_keywords(output_dir: Path, stem: str) -> dict[str, Any] | None:
    data = _load(output_dir / f"{stem}.keywords.json")
    return data if data is not None and "keywords" in data else None


def load_graph(output_dir: Path, stem: str) -> dict[str, Any] | None:
    data = _load(output_dir / f"{stem}.graph.json")
    return data if data is not None and "graph" in data else None


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
