"""whisper.cpp inference engine for the live mode.

Wraps pywhispercpp with the model resident in-process (loaded once, behind a
lock). Kept behind a narrow interface (``transcribe_partial`` /
``transcribe_final``) so the fallback engine (whisper-server over HTTP, or a
CPU-only quantization) can be swapped in without touching callers.

This module must stay importable without pywhispercpp installed — the import
happens lazily on first use so the batch-only deployment keeps working.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from .. import config
from .streaming import Engine

logger = logging.getLogger(__name__)

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
            segments = self._model.transcribe(audio, **self._filter_params(params))
        return "".join(segment.text for segment in segments).strip()

    @staticmethod
    def _filter_params(params: dict) -> dict:
        """Drop kwargs the installed pywhispercpp build does not know about,
        so a binding version mismatch degrades quality instead of raising."""
        try:
            from pywhispercpp.constants import PARAMS_SCHEMA

            valid = set(PARAMS_SCHEMA.keys())
        except Exception:
            return params
        return {key: value for key, value in params.items() if key in valid}


_engine: Engine | None = None
_engine_lock = threading.Lock()


def _cuda_available() -> bool:
    """True when torch sees a CUDA device (import failure counts as False)."""
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _moonshine_weights_present() -> bool:
    return (Path(config.LIVE_MOONSHINE_MODEL_DIR) / "model.safetensors").exists()


def _ggml_weights_present() -> bool:
    return Path(config.LIVE_MODEL_PATH).exists()


def _create_engine(choice: str) -> Engine:
    """Resolve LIVE_ENGINE (auto|whispercpp|moonshine) to a concrete engine.

    The selection (engine + reason) is always logged. ``auto`` prefers the
    CUDA whisper.cpp path, so GPU hosts keep their existing behaviour;
    moonshine is picked only when its weights were explicitly fetched (opt-in).
    """
    choice = (choice or "auto").strip().lower()
    if choice == "whispercpp":
        logger.info("live engine: whispercpp (LIVE_ENGINE=whispercpp)")
        return LiveEngine()
    if choice == "moonshine":
        logger.info("live engine: moonshine (LIVE_ENGINE=moonshine)")
        # Lazy import: engine_moonshine imports LiveEngineError from this
        # module, so a top-level import here would be circular.
        from . import engine_moonshine

        return engine_moonshine.MoonshineEngine()
    if choice == "auto":
        if _cuda_available():
            logger.info("live engine: whispercpp (auto: CUDA available)")
            return LiveEngine()
        if _moonshine_weights_present():
            logger.info(
                "live engine: moonshine (auto: no CUDA, moonshine weights present)"
            )
            from . import engine_moonshine

            return engine_moonshine.MoonshineEngine()
        if _ggml_weights_present():
            logger.info(
                "live engine: whispercpp CPU (auto: no CUDA, no moonshine weights)"
            )
            return LiveEngine()
        raise LiveEngineError(
            "no live engine weights found — run "
            "`python scripts/fetch_live_model.py` (whisper.cpp GPU/CPU) or "
            "`python scripts/fetch_moonshine_model.py` (moonshine CPU)"
        )
    raise LiveEngineError(
        f"invalid LIVE_ENGINE={choice!r} (expected auto|whispercpp|moonshine)"
    )


def get_engine() -> Engine:
    """Return the process-wide live engine, creating it on first use."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = _create_engine(config.LIVE_ENGINE)
        return _engine
