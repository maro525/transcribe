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

# --- Live (realtime) mode ---------------------------------------------------
# Quantization default is q8_0: near-lossless vs f16 for large-v3-turbo at
# roughly half the size/VRAM (~870 MB vs ~1.6 GB), and acceptable on CPU.
# q5_0 (~550 MB) is available via LIVE_MODEL_QUANT for RAM-constrained hosts.
LIVE_MODEL_QUANT = os.environ.get("LIVE_MODEL_QUANT", "q8_0")
_default_live_model = (
    BASE_DIR / "models" / f"ggml-large-v3-turbo-{LIVE_MODEL_QUANT}.bin"
    if LIVE_MODEL_QUANT != "f16"
    else BASE_DIR / "models" / "ggml-large-v3-turbo.bin"
)
LIVE_MODEL_PATH = Path(
    os.environ.get("LIVE_MODEL_PATH", _default_live_model)
).expanduser()
LIVE_LANGUAGE = os.environ.get("LIVE_LANGUAGE", "ja")
LIVE_WHISPER_THREADS = int(
    os.environ.get("LIVE_WHISPER_THREADS", str(os.cpu_count() or 4))
)

LIVE_VAD_THRESHOLD = float(os.environ.get("LIVE_VAD_THRESHOLD", "0.5"))
LIVE_VAD_MIN_SILENCE_MS = int(os.environ.get("LIVE_VAD_MIN_SILENCE_MS", "500"))
LIVE_MIN_UTTERANCE_MS = int(os.environ.get("LIVE_MIN_UTTERANCE_MS", "300"))
LIVE_MAX_UTTERANCE_SECONDS = float(os.environ.get("LIVE_MAX_UTTERANCE_SECONDS", "30"))
LIVE_PREROLL_MS = int(os.environ.get("LIVE_PREROLL_MS", "300"))

LIVE_PARTIAL_INTERVAL_SECONDS = float(
    os.environ.get("LIVE_PARTIAL_INTERVAL_SECONDS", "1.0")
)
LIVE_PARTIAL_WINDOW_SECONDS = float(
    os.environ.get("LIVE_PARTIAL_WINDOW_SECONDS", "15")
)

LIVE_KEYWORD_LIMIT = int(os.environ.get("LIVE_KEYWORD_LIMIT", "15"))
LIVE_FINAL_HISTORY_SIZE = 100
LIVE_DISCONNECT_FINALIZE_SECONDS = int(
    os.environ.get("LIVE_DISCONNECT_FINALIZE_SECONDS", "60")
)


def ensure_directories() -> None:
    for directory in [SOURCE_DIR, OUTPUT_DIR, DONE_DIR, TEMP_DIR, CACHE_DIR]:
        directory.mkdir(parents=True, exist_ok=True)
