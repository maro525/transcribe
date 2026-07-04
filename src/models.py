"""Batch-pipeline model loading and the shared model registry.

The registry caches the batch models (openai-whisper, pyannote, audio
cropper) as module-level singletons behind a lock, so the worker and any
future callers share one loaded instance per process. The live mode's
whisper.cpp model is intentionally NOT managed here — it lives in
``src/live/engine.py`` (see task NSKETCH-819, decision #7 revised).
"""

import os
import threading
from pathlib import Path

import torch
import whisper
from pyannote.audio import Audio, Pipeline


def get_device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def load_whisper_model(name: str = "medium"):
    return whisper.load_model(name)


def load_diarization_pipeline(
    hf_token: str,
    cache_dir: Path,
    model_name: str = "pyannote/speaker-diarization-3.1",
    device: str | None = None,
):
    os.environ["XDG_CACHE_HOME"] = str(cache_dir)
    pipeline = Pipeline.from_pretrained(
        model_name,
        use_auth_token=hf_token,
        cache_dir=str(cache_dir),
    )
    pipeline.to(torch.device(device or get_device()))
    return pipeline


def make_audio_cropper(sample_rate: int = 16000) -> Audio:
    return Audio(sample_rate=sample_rate, mono=True)


# --- Shared registry (batch models only) ------------------------------------

_registry_lock = threading.Lock()
_whisper_model = None
_diarization_pipeline = None
_audio_cropper: Audio | None = None


def get_whisper_model(name: str = "medium"):
    """Return the process-wide whisper model, loading it on first use."""
    global _whisper_model
    with _registry_lock:
        if _whisper_model is None:
            _whisper_model = load_whisper_model(name)
        return _whisper_model


def get_diarization_pipeline(
    hf_token: str,
    cache_dir: Path,
    model_name: str = "pyannote/speaker-diarization-3.1",
    device: str | None = None,
):
    """Return the process-wide diarization pipeline, loading it on first use."""
    global _diarization_pipeline
    with _registry_lock:
        if _diarization_pipeline is None:
            _diarization_pipeline = load_diarization_pipeline(
                hf_token=hf_token,
                cache_dir=cache_dir,
                model_name=model_name,
                device=device,
            )
        return _diarization_pipeline


def get_audio_cropper(sample_rate: int = 16000) -> Audio:
    """Return the process-wide audio cropper, creating it on first use."""
    global _audio_cropper
    with _registry_lock:
        if _audio_cropper is None:
            _audio_cropper = make_audio_cropper(sample_rate)
        return _audio_cropper
