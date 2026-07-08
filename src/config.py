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

# --- Discourse structure extraction (batch artifacts) ------------------------
# Primary extractor is Claude via the official anthropic SDK; the API key is
# resolved from ANTHROPIC_API_KEY by the SDK (never hardcoded). When the key
# or the SDK is absent — or the API call fails — batch processing falls back
# to the deterministic marker-based extractor (src/discourse.py) and the job
# still completes. Set DISCOURSE_ENABLED=0 to skip structure generation.
DISCOURSE_ENABLED = os.environ.get("DISCOURSE_ENABLED", "1") not in ("0", "false", "")
DISCOURSE_MODEL = os.environ.get("DISCOURSE_MODEL", "claude-opus-4-8")
DISCOURSE_EFFORT = os.environ.get("DISCOURSE_EFFORT", "high")
# Meeting-length structure JSON can be large; streaming is used so a big
# max_tokens does not trip SDK timeout guards.
DISCOURSE_MAX_TOKENS = int(os.environ.get("DISCOURSE_MAX_TOKENS", "32000"))

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

# --- Live engine selection ---------------------------------------------------
# Default (unset/empty): moonshine (CPU-first). Fetch its weights with
# scripts/fetch_moonshine_model.py before first use.
# Set whispercpp to force whisper.cpp, or auto for a hardware-aware pick:
#   auto = CUDA host -> whispercpp; non-CUDA + moonshine weights -> moonshine;
#          else whisper.cpp CPU.
# Note: GPU hosts must opt in with LIVE_ENGINE=auto (or whispercpp) now that
# moonshine is the default.
LIVE_ENGINE = (os.environ.get("LIVE_ENGINE") or "moonshine").strip().lower()
LIVE_MOONSHINE_MODEL_DIR = Path(
    os.environ.get("LIVE_MOONSHINE_MODEL_DIR", BASE_DIR / "models" / "moonshine-tiny-ja")
).expanduser()
# Fixed-length chunking keeps each decode under the moonshine decoder's
# ~194-token cap (model card: max tokens ~= seconds * 13 -> ~14.9 s ceiling).
LIVE_MOONSHINE_CHUNK_SECONDS = float(
    os.environ.get("LIVE_MOONSHINE_CHUNK_SECONDS", "12.0")
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
# Word-network panel: per-final term extraction feeds a candidate pool; the
# displayed nodes are the session-cumulative salience top-N (see live/graph.py).
LIVE_GRAPH_MAX_NODES = int(os.environ.get("LIVE_GRAPH_MAX_NODES", "40"))
# Candidate terms taken from one finalized utterance. LIVE_GRAPH_WORDS_PER_FINAL
# is honored as a deprecated alias for backward compatibility.
LIVE_GRAPH_CANDIDATES_PER_FINAL = int(
    os.environ.get(
        "LIVE_GRAPH_CANDIDATES_PER_FINAL",
        os.environ.get("LIVE_GRAPH_WORDS_PER_FINAL", "20"),
    )
)
# Display cutoffs: minimum cumulative salience / minimum appearance count
# (number of finals) a candidate needs before it becomes a visible node.
LIVE_GRAPH_MIN_SALIENCE = float(os.environ.get("LIVE_GRAPH_MIN_SALIENCE", "0.0"))
LIVE_GRAPH_MIN_FREQUENCY = int(os.environ.get("LIVE_GRAPH_MIN_FREQUENCY", "1"))
# Exponential per-final decay applied to node salience and edge weights.
# 1.0 disables decay (default); 0.98 is a reasonable starting point.
LIVE_GRAPH_DECAY = float(os.environ.get("LIVE_GRAPH_DECAY", "1.0"))
# Upper bound of the internal candidate pool (memory guard).
LIVE_GRAPH_MAX_CANDIDATES = int(os.environ.get("LIVE_GRAPH_MAX_CANDIDATES", "200"))
LIVE_FINAL_HISTORY_SIZE = 100
LIVE_DISCONNECT_FINALIZE_SECONDS = int(
    os.environ.get("LIVE_DISCONNECT_FINALIZE_SECONDS", "60")
)


def ensure_directories() -> None:
    for directory in [SOURCE_DIR, OUTPUT_DIR, DONE_DIR, TEMP_DIR, CACHE_DIR]:
        directory.mkdir(parents=True, exist_ok=True)
