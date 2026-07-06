"""Co-occurrence graph aggregation for the live "word network" panel.

Each finalized utterance contributes candidate terms (with extraction scores)
to a session-cumulative candidate pool; every pair of terms co-occurring in
the same utterance forms an edge. The *displayed* nodes are selected at
snapshot time: candidates passing the salience/frequency cutoffs, ranked by
cumulative salience, top ``max_nodes``. Candidates that fall out of display
keep accumulating and can come back later. Optional exponential per-final
decay (``decay < 1.0``) lets older topics fade.

Wire format is unchanged from the original implementation: node ``weight``
is the appearance count (number of finals, int) and edge ``weight`` is the
co-occurrence count (float only when decay is active) — the frontend canvas
renderer needs no changes.

Not thread-safe on its own: callers mutate it under their own lock (mirroring
the existing keyword recomputation in LiveSessionManager).
"""
from __future__ import annotations

from typing import Any

from .terms import Term

_EDGE_EPSILON = 1e-3  # decayed edges below this are dropped


class CooccurrenceGraph:
    def __init__(
        self,
        max_nodes: int,
        *,
        decay: float = 1.0,
        min_salience: float = 0.0,
        min_frequency: int = 1,
        max_candidates: int = 200,
    ) -> None:
        self._max_nodes = max(1, int(max_nodes))
        self._decay = float(decay)
        self._min_salience = float(min_salience)
        self._min_frequency = int(min_frequency)
        self._max_candidates = max(self._max_nodes, int(max_candidates))
        self._seq = 0
        # word -> {"salience": float, "frequency": int, "last_seen": int}
        self._nodes: dict[str, dict[str, Any]] = {}
        # (a, b) with a <= b -> co-occurrence weight (int unless decay active)
        self._edges: dict[tuple[str, str], float] = {}

    def add_utterance(self, terms: list[Term] | list[str]) -> None:
        """Register one finalized utterance's terms (dedup, order kept).

        Plain strings are accepted for backward compatibility and map to
        ``Term(word, 1.0)`` — salience then equals frequency, matching the
        original ranking behaviour.
        """
        scored: dict[str, float] = {}
        for item in terms:
            word = item.word if isinstance(item, Term) else item
            score = item.score if isinstance(item, Term) else 1.0
            if word and word not in scored:
                scored[word] = float(score)
        if not scored:
            return
        self._seq += 1
        if self._decay < 1.0:
            for node in self._nodes.values():
                node["salience"] *= self._decay
            self._edges = {
                key: weight * self._decay
                for key, weight in self._edges.items()
                if weight * self._decay > _EDGE_EPSILON
            }
        for word, score in scored.items():
            node = self._nodes.get(word)
            if node is None:
                self._nodes[word] = {
                    "salience": score,
                    "frequency": 1,
                    "last_seen": self._seq,
                }
            else:
                node["salience"] += score
                node["frequency"] += 1
                node["last_seen"] = self._seq
        words = list(scored)
        for i in range(len(words)):
            for j in range(i + 1, len(words)):
                key = self._edge_key(words[i], words[j])
                self._edges[key] = self._edges.get(key, 0) + 1
        self._prune_candidates()

    def snapshot(self) -> dict[str, Any]:
        """Full-snapshot WS message (nodes always reference-complete).

        Selection happens here: cutoff-passing candidates ranked by cumulative
        salience (ties: frequency, recency, word), top ``max_nodes`` visible.
        Node ``weight`` stays the int appearance count (wire format contract).
        """
        visible = [
            word
            for word, node in self._nodes.items()
            if node["salience"] >= self._min_salience
            and node["frequency"] >= self._min_frequency
        ]
        visible.sort(
            key=lambda word: (
                -self._nodes[word]["salience"],
                -self._nodes[word]["frequency"],
                -self._nodes[word]["last_seen"],
                word,
            )
        )
        chosen = set(visible[: self._max_nodes])
        return {
            "type": "graph",
            "seq": self._seq,
            "nodes": [
                {
                    "id": word,
                    "weight": int(node["frequency"]),
                    "last_seen": int(node["last_seen"]),
                }
                for word, node in self._nodes.items()
                if word in chosen
            ],
            "edges": [
                {"a": a, "b": b, "weight": weight}
                for (a, b), weight in self._edges.items()
                if a in chosen and b in chosen
            ],
        }

    def reset(self) -> None:
        self._seq = 0
        self._nodes.clear()
        self._edges.clear()

    @staticmethod
    def _edge_key(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a <= b else (b, a)

    def _prune_candidates(self) -> None:
        """Bound the internal pool: evict lowest-salience, then oldest words.

        This is a memory guard only — display selection lives in snapshot().
        """
        while len(self._nodes) > self._max_candidates:
            victim = min(
                self._nodes,
                key=lambda word: (
                    self._nodes[word]["salience"],
                    self._nodes[word]["last_seen"],
                    word,
                ),
            )
            del self._nodes[victim]
            self._edges = {
                key: weight for key, weight in self._edges.items() if victim not in key
            }
