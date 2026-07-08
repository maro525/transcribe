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
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config
from .discourse import RelationExtractor, Utterance, build_structure
from .live.graph import CooccurrenceGraph
from .live.terms import extract_terms

SCHEMA_VERSION = 1
STRUCTURE_KIND = "logical_structure"


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
    return data if data is not None and "graph" in data else None


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
