import os
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
