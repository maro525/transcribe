"""whisper.cpp inference engine for the live mode.

Wraps pywhispercpp with the model resident in-process (loaded once, behind a
lock). Kept behind a narrow interface (``transcribe_partial`` /
``transcribe_final``) so the fallback engine (whisper-server over HTTP, or a
CPU-only quantization) can be swapped in without touching callers.

This module must stay importable without pywhispercpp installed — the import
happens lazily on first use so the batch-only deployment keeps working.
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from .. import config

# whisper.cpp rejects buffers shorter than ~1s; pad with trailing silence.
_MIN_AUDIO_SECONDS = 1.1
_SAMPLE_RATE = config.SAMPLE_RATE


class LiveEngineError(RuntimeError):
    """Raised when the live engine cannot be initialised or run."""


class LiveEngine:
    """Resident whisper.cpp model with partial/final inference presets."""

    def __init__(
        self,
        model_path: Path = config.LIVE_MODEL_PATH,
        n_threads: int = config.LIVE_WHISPER_THREADS,
        language: str = config.LIVE_LANGUAGE,
    ) -> None:
        if not Path(model_path).exists():
            raise LiveEngineError(
                f"live model not found: {model_path} — "
                "run `python scripts/fetch_live_model.py` first"
            )
        try:
            from pywhispercpp.model import Model
        except ImportError as error:
            raise LiveEngineError(
                "pywhispercpp is not installed — run `pip install -r requirements.txt`"
            ) from error

        self._language = language
        self._infer_lock = threading.Lock()
        self._model = Model(
            str(model_path),
            n_threads=n_threads,
            print_realtime=False,
            print_progress=False,
        )

    def transcribe_partial(self, audio: np.ndarray) -> str:
        """Low-latency pass over an in-progress utterance buffer.

        greedy decode, no cross-call context, single segment.
        """
        return self._transcribe(
            audio,
            language=self._language,
            temperature=0.0,
            no_context=True,
            single_segment=True,
        )

    def transcribe_final(self, audio: np.ndarray) -> str:
        """Full-quality pass over a complete utterance buffer."""
        return self._transcribe(
            audio,
            language=self._language,
            temperature=0.0,
            no_context=True,
        )

    def _transcribe(self, audio: np.ndarray, **params) -> str:
        audio = np.asarray(audio, dtype=np.float32)
        min_samples = int(_MIN_AUDIO_SECONDS * _SAMPLE_RATE)
        if audio.size < min_samples:
            audio = np.pad(audio, (0, min_samples - audio.size))
        with self._infer_lock:
            segments = self._model.transcribe(audio, **params)
        return "".join(segment.text for segment in segments).strip()


_engine: LiveEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> LiveEngine:
    """Return the process-wide live engine, loading the model on first use."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = LiveEngine()
        return _engine
