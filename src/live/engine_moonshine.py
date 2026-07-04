"""Moonshine (transformers) live engine — CPU-friendly Japanese ASR.

Adapter behind the same narrow interface as ``LiveEngine``
(``transcribe_partial`` / ``transcribe_final``: np.float32 16 kHz mono -> str).
Only the transcription backend is swapped; VAD, partial/final scheduling,
frozen-prefix handling, warmup and in-flight suppression are reused unchanged
from streaming.py.

Model: UsefulSensors/moonshine-tiny-ja (27M params, Japanese-only — the
``LIVE_LANGUAGE`` setting is ignored by this engine). Long utterances are split
into fixed ``LIVE_MOONSHINE_CHUNK_SECONDS`` windows and decoded sequentially,
then joined, to stay under the decoder's ~194-token cap (model card
recommendation: max tokens ~= seconds * 13, i.e. a ~14.9 s ceiling).

License note: the weights are distributed under the Moonshine AI Community
License (NOT MIT) — commercial use requires (free, <$1M revenue) registration.
See scripts/fetch_moonshine_model.py.

This module MUST stay importable without transformers/torch installed — heavy
imports happen lazily on first construction (same discipline as engine.py), so
the pure helpers below are unit-testable in a numpy-only environment.
"""
from __future__ import annotations

import math
import re
import threading
from pathlib import Path

import numpy as np

from .. import config
from .engine import LiveEngineError

_SAMPLE_RATE = config.SAMPLE_RATE
# Model-card recommended decode budget (anti-hallucination-loop cap).
TOKENS_PER_SEC = 13
# The decoder tops out at ~194 tokens -> ~14.9 s of audio per decode.
_DECODER_TOKEN_CAP = 194
MAX_CHUNK_SECONDS = _DECODER_TOKEN_CAP / TOKENS_PER_SEC
_MIN_TRANSFORMERS = (4, 52)
_WARMUP_SECONDS = 0.5


def chunk_spans(n_samples: int, chunk_samples: int) -> list[tuple[int, int]]:
    """Fixed-length ``[start, end)`` spans covering ``n_samples``.

    Pure function (no torch/transformers) so chunking stays unit-testable in
    the numpy-only environment.
    """
    if chunk_samples <= 0:
        raise ValueError("chunk_samples must be positive")
    if n_samples <= 0:
        return []
    return [
        (start, min(start + chunk_samples, n_samples))
        for start in range(0, n_samples, chunk_samples)
    ]


def token_budget(seconds: float, tokens_per_sec: int = TOKENS_PER_SEC) -> int:
    """``max_new_tokens`` for a chunk of the given duration (always >= 1)."""
    return max(1, int(math.ceil(seconds * tokens_per_sec)))


class MoonshineEngine:
    """Resident Moonshine model with chunked greedy decode for partial/final."""

    def __init__(
        self,
        model_dir: Path = config.LIVE_MOONSHINE_MODEL_DIR,
        chunk_seconds: float = config.LIVE_MOONSHINE_CHUNK_SECONDS,
        n_threads: int = config.LIVE_WHISPER_THREADS,
    ) -> None:
        if not 0 < chunk_seconds <= MAX_CHUNK_SECONDS:
            raise LiveEngineError(
                f"LIVE_MOONSHINE_CHUNK_SECONDS={chunk_seconds} out of range — "
                f"must be > 0 and <= {MAX_CHUNK_SECONDS:.1f} "
                f"(decoder cap of {_DECODER_TOKEN_CAP} tokens at "
                f"{TOKENS_PER_SEC} tokens/sec)"
            )
        model_dir = Path(model_dir)
        if not (model_dir / "model.safetensors").exists():
            raise LiveEngineError(
                f"moonshine weights not found: {model_dir} — "
                "run `python scripts/fetch_moonshine_model.py` first"
            )
        torch, transformers = self._require_runtime()
        # Share the torch CPU runtime with Silero VAD; default = CPU cores.
        torch.set_num_threads(n_threads)

        self._torch = torch
        self._chunk_samples = int(chunk_seconds * _SAMPLE_RATE)
        self._infer_lock = threading.Lock()
        self._processor = transformers.AutoProcessor.from_pretrained(
            str(model_dir), local_files_only=True
        )
        self._model = transformers.MoonshineForConditionalGeneration.from_pretrained(
            str(model_dir), local_files_only=True
        ).eval()
        # Absorb first-inference setup cost into the worker's warmup step.
        self._generate(
            np.zeros(int(_WARMUP_SECONDS * _SAMPLE_RATE), dtype=np.float32),
            max_new_tokens=1,
        )

    def transcribe_partial(self, audio: np.ndarray) -> str:
        """Low-latency pass over an in-progress utterance buffer.

        Moonshine needs no whisper-style parameter split (greedy, no context),
        so partial and final share one implementation.
        """
        return self._transcribe(audio)

    def transcribe_final(self, audio: np.ndarray) -> str:
        """Full pass over a complete utterance buffer."""
        return self._transcribe(audio)

    def _transcribe(self, audio: np.ndarray) -> str:
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return ""
        parts: list[str] = []
        for start, end in chunk_spans(audio.size, self._chunk_samples):
            segment = audio[start:end]
            budget = token_budget(segment.size / _SAMPLE_RATE)
            text = self._generate(segment, max_new_tokens=budget)
            if text:
                parts.append(text)
        return "".join(parts).strip()

    def _generate(self, audio: np.ndarray, *, max_new_tokens: int) -> str:
        inputs = self._processor(
            audio, sampling_rate=_SAMPLE_RATE, return_tensors="pt"
        )
        with self._infer_lock, self._torch.no_grad():
            ids = self._model.generate(**inputs, max_new_tokens=max_new_tokens)
        return self._processor.batch_decode(ids, skip_special_tokens=True)[0].strip()

    @staticmethod
    def _require_runtime():
        try:
            import torch
            import transformers
        except ImportError as error:
            raise LiveEngineError(
                "transformers/torch is not installed — "
                "run `pip install -r requirements.txt`"
            ) from error
        numbers = re.findall(r"\d+", transformers.__version__)
        version = tuple(int(x) for x in numbers[:2]) if numbers else (0, 0)
        if version < _MIN_TRANSFORMERS:
            raise LiveEngineError(
                f"transformers>={'.'.join(map(str, _MIN_TRANSFORMERS))} is required "
                f"for Moonshine (found {transformers.__version__}) — "
                "run `pip install -r requirements.txt`"
            )
        return torch, transformers
