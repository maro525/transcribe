"""Voice activity detection and utterance segmentation.

Two layers, kept separate for testability:

- ``SileroVad``: thin wrapper over the silero-vad model (torch backend,
  already a dependency via pyannote). Imported lazily.
- ``UtteranceSegmenter``: a pure state machine that consumes 16 kHz float32
  PCM, asks a probability function about each 512-sample (32 ms) frame, and
  emits utterance lifecycle events. It has no torch dependency, so the
  start/end/preroll/min/max logic is unit-testable with synthetic
  probabilities.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque

import numpy as np

from .. import config

FRAME_SIZE = 512  # samples @ 16 kHz = 32 ms — Silero VAD's expected frame
_SAMPLE_RATE = config.SAMPLE_RATE
_FRAME_MS = FRAME_SIZE * 1000 / _SAMPLE_RATE


class SileroVad:
    """Speech-probability oracle backed by the Silero VAD model."""

    def __init__(self) -> None:
        try:
            import torch
            from silero_vad import load_silero_vad
        except ImportError as error:
            raise RuntimeError(
                "silero-vad / torch is not installed — "
                "run `pip install -r requirements.txt`"
            ) from error
        self._torch = torch
        self._model = load_silero_vad()

    def probability(self, frame: np.ndarray) -> float:
        """Speech probability for one 512-sample float32 frame."""
        tensor = self._torch.from_numpy(np.ascontiguousarray(frame, dtype=np.float32))
        with self._torch.no_grad():
            return float(self._model(tensor, _SAMPLE_RATE).item())

    def reset(self) -> None:
        self._model.reset_states()


@dataclass(frozen=True)
class UtteranceEvent:
    """Lifecycle event emitted by the segmenter.

    kind:
      - "start":  speech onset detected (audio = preroll + first frame)
      - "update": utterance grew (audio = full accumulated buffer)
      - "end":    utterance finalized (audio = full buffer incl. trailing silence)
      - "cancel": utterance discarded (too short — noise/cough)
    """

    kind: str
    utterance_id: int
    audio: np.ndarray
    t0: float
    t1: float | None = None


class UtteranceSegmenter:
    """Turns a stream of PCM samples into utterance events.

    - speech start: probability >= threshold (preroll frames are prepended)
    - speech end: silence lasting >= min_silence_ms
    - force finalize: utterance length >= max_utterance_seconds
    - discard: speech portion shorter than min_utterance_ms
    """

    def __init__(
        self,
        prob_fn: Callable[[np.ndarray], float],
        *,
        threshold: float = config.LIVE_VAD_THRESHOLD,
        min_silence_ms: int = config.LIVE_VAD_MIN_SILENCE_MS,
        min_utterance_ms: int = config.LIVE_MIN_UTTERANCE_MS,
        max_utterance_seconds: float = config.LIVE_MAX_UTTERANCE_SECONDS,
        preroll_ms: int = config.LIVE_PREROLL_MS,
    ) -> None:
        self._prob_fn = prob_fn
        self._threshold = threshold
        self._min_silence_frames = max(1, int(min_silence_ms / _FRAME_MS))
        self._min_utterance_frames = max(1, int(min_utterance_ms / _FRAME_MS))
        self._max_utterance_frames = max(
            self._min_utterance_frames + 1,
            int(max_utterance_seconds * 1000 / _FRAME_MS),
        )
        preroll_frames = max(0, int(preroll_ms / _FRAME_MS))
        self._preroll: Deque[np.ndarray] = deque(maxlen=preroll_frames)

        self._pending = np.empty(0, dtype=np.float32)
        self._in_speech = False
        self._frames: list[np.ndarray] = []
        self._speech_frames = 0
        self._silence_run = 0
        self._total_frames = 0  # session-relative clock
        self._utterance_start_frame = 0
        self._next_id = 1

    def process(self, samples: np.ndarray) -> list[UtteranceEvent]:
        """Feed PCM (float32, 16 kHz, mono); return lifecycle events."""
        events: list[UtteranceEvent] = []
        samples = np.asarray(samples, dtype=np.float32)
        buffer = np.concatenate([self._pending, samples])

        offset = 0
        grew = False
        while offset + FRAME_SIZE <= buffer.size:
            frame = buffer[offset : offset + FRAME_SIZE]
            offset += FRAME_SIZE
            frame_events, frame_grew = self._process_frame(frame)
            events.extend(frame_events)
            grew = grew or frame_grew
        self._pending = buffer[offset:]

        if grew and self._in_speech:
            events.append(self._make_event("update"))
        return events

    def flush(self) -> list[UtteranceEvent]:
        """Finalize any in-progress utterance (called on session stop)."""
        if not self._in_speech:
            return []
        return [self._finalize_utterance()]

    # --- internals -----------------------------------------------------

    def _process_frame(
        self, frame: np.ndarray
    ) -> tuple[list[UtteranceEvent], bool]:
        events: list[UtteranceEvent] = []
        grew = False
        is_speech = self._prob_fn(frame) >= self._threshold
        self._total_frames += 1

        if not self._in_speech:
            if is_speech:
                self._in_speech = True
                self._frames = [*self._preroll, frame]
                self._speech_frames = 1
                self._silence_run = 0
                self._utterance_start_frame = self._total_frames - len(self._frames)
                events.append(self._make_event("start"))
            else:
                self._preroll.append(frame)
            return events, grew

        self._frames.append(frame)
        grew = True
        if is_speech:
            self._speech_frames += 1
            self._silence_run = 0
        else:
            self._silence_run += 1
            if self._silence_run >= self._min_silence_frames:
                events.append(self._finalize_utterance())
                return events, False

        if len(self._frames) >= self._max_utterance_frames:
            events.append(self._finalize_utterance())
            return events, False
        return events, grew

    def _finalize_utterance(self) -> UtteranceEvent:
        too_short = self._speech_frames < self._min_utterance_frames
        kind = "cancel" if too_short else "end"
        event = self._make_event(kind, closing=True)

        self._in_speech = False
        self._frames = []
        self._speech_frames = 0
        self._silence_run = 0
        self._preroll.clear()
        self._next_id += 1
        return event

    def _make_event(self, kind: str, closing: bool = False) -> UtteranceEvent:
        audio = (
            np.concatenate(self._frames)
            if self._frames
            else np.empty(0, dtype=np.float32)
        )
        t0 = self._utterance_start_frame * _FRAME_MS / 1000
        t1 = self._total_frames * _FRAME_MS / 1000 if closing else None
        return UtteranceEvent(
            kind=kind,
            utterance_id=self._next_id,
            audio=audio,
            t0=round(t0, 2),
            t1=round(t1, 2) if t1 is not None else None,
        )
