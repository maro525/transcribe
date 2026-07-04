"""Transcription worker: turns utterance events into partial/final messages.

One dedicated thread owns all whisper.cpp inference. The audio-feed side
(WS receive -> VAD) never blocks on inference; it only enqueues jobs.

Scheduling rules (task NSKETCH-819 design):
- while an utterance is active, run a low-latency *partial* pass at most every
  ``partial_interval`` seconds, over the last ``partial_window`` seconds only
  (text for audio before the window is frozen from the previous partial and
  re-prepended);
- when an utterance ends, run a full *final* pass over the whole buffer.
  Finals have priority: stale partials for a finalized utterance are skipped;
- a cancelled (too short) utterance yields an empty final so the client can
  clear any partial line it may have shown.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable, Protocol

import numpy as np

from .. import config


class Engine(Protocol):
    def transcribe_partial(self, audio: np.ndarray) -> str: ...

    def transcribe_final(self, audio: np.ndarray) -> str: ...


EmitFn = Callable[[dict[str, Any]], None]

_STOP = object()


class TranscriptionWorker:
    def __init__(
        self,
        engine_provider: Callable[[], Engine],
        emit: EmitFn,
        *,
        partial_interval: float = config.LIVE_PARTIAL_INTERVAL_SECONDS,
        partial_window_seconds: float = config.LIVE_PARTIAL_WINDOW_SECONDS,
        sample_rate: int = config.SAMPLE_RATE,
    ) -> None:
        self._engine_provider = engine_provider
        self._engine: Engine | None = None
        self._emit = emit
        self._partial_interval = partial_interval
        self._window_samples = int(partial_window_seconds * sample_rate)

        self._queue: queue.Queue[Any] = queue.Queue()
        self._thread: threading.Thread | None = None
        # Guards the shared scheduling state below (mutated from both the
        # audio-feed thread and the worker thread).
        self._state_lock = threading.Lock()
        self._finalized_ids: set[int] = set()
        self._partial_inflight: set[int] = set()
        self._last_partial_at: dict[int, float] = {}
        # per-utterance frozen prefix: id -> (frozen_text, frozen_upto_samples)
        self._frozen: dict[int, tuple[str, int]] = {}
        self._covered: dict[int, int] = {}
        self._last_text: dict[int, str] = {}

    # --- feed-side API (called from the audio thread) --------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="live-transcription", daemon=True
        )
        self._thread.start()
        self._queue.put(("warmup",))

    def stop(self, wait_seconds: float = 60.0) -> None:
        """Process remaining jobs, then stop the thread."""
        self._queue.put(_STOP)
        if self._thread is not None:
            self._thread.join(timeout=wait_seconds)
            self._thread = None

    def on_utterance_update(self, utterance_id: int, audio: np.ndarray, t0: float) -> None:
        with self._state_lock:
            if utterance_id in self._finalized_ids:
                return
            if utterance_id in self._partial_inflight:
                return
            now = time.monotonic()
            last = self._last_partial_at.get(utterance_id)
            if last is not None and now - last < self._partial_interval:
                return
            self._last_partial_at[utterance_id] = now
            self._partial_inflight.add(utterance_id)
        self._queue.put(("partial", utterance_id, np.array(audio, copy=True), t0))

    def on_utterance_end(
        self, utterance_id: int, audio: np.ndarray, t0: float, t1: float
    ) -> None:
        with self._state_lock:
            self._finalized_ids.add(utterance_id)
        self._queue.put(("final", utterance_id, np.array(audio, copy=True), t0, t1))

    def on_utterance_cancel(self, utterance_id: int, t0: float) -> None:
        with self._state_lock:
            self._finalized_ids.add(utterance_id)
        self._queue.put(("cancel", utterance_id, t0))

    # --- worker thread ----------------------------------------------------

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is _STOP:
                return
            try:
                self._handle(job)
            except Exception as error:  # keep the worker alive on engine errors
                self._emit({"type": "error", "message": str(error)})

    def _handle(self, job: tuple) -> None:
        kind = job[0]
        if kind == "warmup":
            self._get_engine()
        elif kind == "partial":
            _, utterance_id, audio, t0 = job
            try:
                self._run_partial(utterance_id, audio, t0)
            finally:
                with self._state_lock:
                    self._partial_inflight.discard(utterance_id)
        elif kind == "final":
            _, utterance_id, audio, t0, t1 = job
            text = self._get_engine().transcribe_final(audio)
            self._cleanup(utterance_id)
            self._emit(
                {
                    "type": "final",
                    "utterance_id": utterance_id,
                    "text": text,
                    "t0": t0,
                    "t1": t1,
                }
            )
        elif kind == "cancel":
            _, utterance_id, t0 = job
            self._cleanup(utterance_id)
            self._emit(
                {
                    "type": "final",
                    "utterance_id": utterance_id,
                    "text": "",
                    "t0": t0,
                    "t1": t0,
                }
            )

    def _run_partial(self, utterance_id: int, audio: np.ndarray, t0: float) -> None:
        with self._state_lock:
            if utterance_id in self._finalized_ids:
                return  # a final is already queued/sent — skip the stale partial

            frozen_text, frozen_upto = self._frozen.get(utterance_id, ("", 0))
            segment = audio[frozen_upto:]
            if segment.size > self._window_samples:
                # Freeze text up to the point covered by the previous partial
                # so re-inference only spans (roughly) the recent window.
                prev_covered = self._covered.get(utterance_id, 0)
                if prev_covered > frozen_upto:
                    frozen_text += self._last_text.get(utterance_id, "")
                    frozen_upto = prev_covered
                    segment = audio[frozen_upto:]
                    self._frozen[utterance_id] = (frozen_text, frozen_upto)
        if segment.size == 0:
            return

        text = self._get_engine().transcribe_partial(segment)

        with self._state_lock:
            self._covered[utterance_id] = int(audio.size)
            self._last_text[utterance_id] = text
            if utterance_id in self._finalized_ids:
                return  # finalized while we were transcribing — drop the partial
        self._emit(
            {
                "type": "partial",
                "utterance_id": utterance_id,
                "text": frozen_text + text,
                "t0": t0,
            }
        )

    def _cleanup(self, utterance_id: int) -> None:
        with self._state_lock:
            self._frozen.pop(utterance_id, None)
            self._covered.pop(utterance_id, None)
            self._last_text.pop(utterance_id, None)
            self._last_partial_at.pop(utterance_id, None)

    def _get_engine(self) -> Engine:
        if self._engine is None:
            self._engine = self._engine_provider()
        return self._engine
