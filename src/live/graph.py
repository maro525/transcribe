"""Co-occurrence graph aggregation for the live "word network" panel.

Each finalized utterance contributes its top keywords as nodes; every pair of
keywords co-occurring in the same utterance forms an edge. Node weight = number
of finals the word appeared in; edge weight = co-occurrence count.

Not thread-safe on its own: callers mutate it under their own lock (mirroring
the existing keyword recomputation in LiveSessionManager).
"""
from __future__ import annotations

from typing import Any


class CooccurrenceGraph:
    def __init__(self, max_nodes: int) -> None:
        self._max_nodes = max(1, int(max_nodes))
        self._seq = 0
        # word -> {"weight": int, "last_seen": int}
        self._nodes: dict[str, dict[str, int]] = {}
        # (a, b) with a <= b -> co-occurrence count
        self._edges: dict[tuple[str, str], int] = {}

    def add_utterance(self, words: list[str]) -> None:
        """Register one finalized utterance's keywords (dedup, order kept)."""
        unique = list(dict.fromkeys(word for word in words if word))
        if not unique:
            return
        self._seq += 1
        for word in unique:
            node = self._nodes.get(word)
            if node is None:
                self._nodes[word] = {"weight": 1, "last_seen": self._seq}
            else:
                node["weight"] += 1
                node["last_seen"] = self._seq
        for i in range(len(unique)):
            for j in range(i + 1, len(unique)):
                key = self._edge_key(unique[i], unique[j])
                self._edges[key] = self._edges.get(key, 0) + 1
        self._prune()

    def snapshot(self) -> dict[str, Any]:
        """Full-snapshot WS message (nodes always reference-complete)."""
        return {
            "type": "graph",
            "seq": self._seq,
            "nodes": [
                {"id": word, "weight": node["weight"], "last_seen": node["last_seen"]}
                for word, node in self._nodes.items()
            ],
            "edges": [
                {"a": a, "b": b, "weight": weight}
                for (a, b), weight in self._edges.items()
            ],
        }

    def reset(self) -> None:
        self._seq = 0
        self._nodes.clear()
        self._edges.clear()

    @staticmethod
    def _edge_key(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a <= b else (b, a)

    def _prune(self) -> None:
        """Evict lowest-weight, then oldest nodes past max_nodes; drop their edges."""
        while len(self._nodes) > self._max_nodes:
            victim = min(
                self._nodes,
                key=lambda w: (self._nodes[w]["weight"], self._nodes[w]["last_seen"], w),
            )
            del self._nodes[victim]
            self._edges = {
                key: weight for key, weight in self._edges.items() if victim not in key
            }
