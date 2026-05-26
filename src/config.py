import os
from pathlib import Path

BASE_DIR = Path(os.environ.get("TRANSCRIBE_BASE_DIR", ".")).expanduser().resolve()
SOURCE_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
DONE_DIR = BASE_DIR / "done"
TEMP_DIR = BASE_DIR / "tmp_audio"
CACHE_DIR = BASE_DIR / "model_cache"
ENV_FILE = Path(os.environ.get("TRANSCRIBE_ENV_FILE", BASE_DIR / ".env"))

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "medium")
DIARIZATION_MODEL = "pyannote/speaker-diarization-3.1"
NUM_SPEAKERS = int(os.environ.get("NUM_SPEAKERS", "2"))

SAMPLE_RATE = 16000
MIN_SEGMENT_SECONDS = 0.3
SUPPORTED_EXTENSIONS = (".mp3", ".wav", ".m4a")
WATCH_INTERVAL_SECONDS = 30

WEB_HOST = os.environ.get("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("WEB_PORT", "8000"))


def ensure_directories() -> None:
    for directory in [SOURCE_DIR, OUTPUT_DIR, DONE_DIR, TEMP_DIR, CACHE_DIR]:
        directory.mkdir(parents=True, exist_ok=True)
