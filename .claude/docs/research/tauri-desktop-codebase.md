# Codebase survey: transcribe → Tauri v2 packaging (NSKETCH-TBD)

Source: Explore subagent, 2026-07-26. Repo: /home/dev/src/transcribe (main, clean).

## 1. Entry point & startup
- `main.py` (34 lines): `config.ensure_directories()` → `worker.bootstrap_history()` → `uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, timeout_graceful_shutdown=5)`.
- No CLI args / argparse / sys.argv. Config is 100% env vars. No reload flag; app object passed directly (PyInstaller-friendly).
- Batch worker = daemon thread (`worker.run`) in same process; model loading inside worker thread, HTTP server up immediately.
- Host/port: `src/config.py:96-97` — `WEB_HOST=127.0.0.1`, `WEB_PORT=8000` (fixed default, no dynamic-port support).

## 2. Routes / frontend
- All routes in `src/web/app.py` `create_app()`: GET `/`, `/jobs`, `/jobs/{filename}`, `/jobs/{filename}/transcript` (deprecated), SSE `/events` (heartbeat 15s), `/live`, `/live/status`, WS `/live/ws`.
- No StaticFiles mount, no static dir, no CORS middleware, no npm/bundler. Templates all-inline JS/CSS: index.html 15KB, detail.html 83KB, live.html 30KB, partials.
- Templates dir is `Path(__file__).parent / "templates"` (`app.py:25`) — __file__-relative, PyInstaller hazard.
- Google Fonts CDN in index.html:8-11 & detail.html:9-12 (offline breakage + Tauri CSP block). live.html system fonts only.
- CSWSH guard `app.py:34-45` `_origin_allowed()`: Origin netloc must equal Host header (missing Origin allowed). Tauri WebView origin `http://tauri.localhost` ≠ `127.0.0.1:8000` → **WS handshake rejected (1008)** unless relaxed or UI served from sidecar origin.

## 3. Live mic pipeline (live.html, inline script)
- TARGET_RATE 16000, CHUNK_SAMPLES 2048 (128ms). Sources: `mic` (getUserMedia w/ echoCancellation+noiseSuppression) and `system` (getDisplayMedia video+audio, video tracks stopped).
- AudioWorklet `PCMDownsampler` built from Blob URL (no external .js): mono downmix, linear-interp resample to 16k (no anti-alias), Int16 clamp, transferable 2048-sample frames.
- Wire: raw 16kHz mono s16le PCM, 4096-byte binary WS frames; JSON control `{"type":"start","source":...}` / `{"type":"stop"}`; inbound: status/partial/final/keywords/graph/finalized/error. Auto-reconnect backoff 1s→15s.
- Backend: `app.py:218-232` receive → `asyncio.to_thread(live_manager.feed_pcm)`; `session.py:149-164` appends to `tmp_audio/live_<id>.wav` + Silero VAD (512-sample frames, `src/live/vad.py`) → UtteranceSegmenter → single inference thread (`src/live/streaming.py`). Outbound `asyncio.Queue(maxsize=1000)`, overflow drops.
- `AudioContext` created without `{sampleRate:16000}` (live.html:664).

## 4. Models
### Batch (src/models.py, src/config.py)
- whisper: `whisper.load_model(name)`; device = cuda if available. `resolve_whisper_model()` pure fn: WHISPER_MODEL override; light→medium (~1.4GB), strong→large-v3-turbo, max→large-v3 (GPU only, else degrade to medium).
- pyannote `speaker-diarization-3.1` (gated, needs HF_TOKEN + accepted licenses on 3.1 + segmentation-3.0, ~30MB). `models.py:36` sets `os.environ["XDG_CACHE_HOME"]` mid-flight — AFTER whisper already loaded → whisper caches in `~/.cache/whisper`, pyannote in `BASE_DIR/model_cache` (two cache roots; ordering bug).
- HF_TOKEN: `src/auth.py` — hard failure w/o token → batch worker thread dies (web+live still work). Only HF_TOKEN/ANTHROPIC_API_KEY can come from .env (loaded late); all other config must be real process env (config.py reads at import).

### Live (src/live/engine.py, engine_moonshine.py)
- `LIVE_ENGINE`: `moonshine` (default, CPU-only, never moved to CUDA) | `whispercpp` (pywhispercpp, PyPI wheel CPU-only) | `auto` (CUDA→whispercpp).
- Moonshine `UsefulSensors/moonshine-tiny-ja` (27M, ~108MB), transformers>=4.52, `local_files_only=True` → weights must be pre-fetched. **License: Moonshine AI Community License (ja is NOT MIT)** — registration required, redistribution needs review.
- Silero VAD weights ship inside pip package (`silero_vad/data/*.jit`) → PyInstaller `--collect-data silero_vad`.
- Fetch scripts: `scripts/fetch_live_model.py` (ggml-large-v3-turbo f16/q8_0/q5_0 from ggerganov/whisper.cpp, no token), `scripts/fetch_moonshine_model.py` (snapshot_download, public). Both `__file__`-relative sys.path hacks.
- `models/`, `model_cache/`, `tmp_audio/` gitignored — nothing pre-downloaded.

## 5. ffmpeg
- Explicit: `src/audio.py:21-32` `subprocess.run(["ffmpeg", ...])` — bare name from PATH, no which/fallback; wav inputs short-circuit. No CREATE_NO_WINDOW → console flash on Windows.
- Implicit: openai-whisper `load_audio()` shells to ffmpeg for EVERY batch file (even .wav) — ffmpeg unconditionally required for batch. ffprobe never used.

## 6. Dependencies
- **No pyproject.toml / uv.lock — only requirements.txt** (pip). Key pins: numpy==1.26.4 (forces Python 3.12), numba==0.60.0, pyannote.audio==3.1.1, torch==2.2.2, torchaudio==2.2.2, onnxruntime==1.20.1, transformers>=4.52,<5; unpinned: openai-whisper, fastapi, uvicorn[standard], jinja2, pywhispercpp, silero-vad; optional: janome, anthropic.
- torch CPU wheel ~200MB; cu121 ~2.4GB. CPU sidecar estimate 1.2–1.8GB before weights. Windows ARM64 非対応 (x64 emulation per README).

## 7. Persistence / dirs
- No DB. In-memory StatusStore rebuilt from `output/*.txt` scan at boot.
- `TRANSCRIBE_BASE_DIR` default `"."` (CWD at import!) → input/ output/ done/ tmp_audio/ model_cache/ (+models/). Must inject app-data dir for packaged app (Program Files not writable).
- Artifacts: output/{stem}.txt|.keywords.json|.graph.json|.structure.json, output/meeting_{sid}_live_draft.txt, tmp_audio/live_{sid}.wav, input/meeting_{sid}.wav (live→batch handoff via shutil.move), done/{original}.

## 8. Env var inventory (40 vars)
TRANSCRIBE_BASE_DIR, TRANSCRIBE_ENV_FILE, WEB_HOST, WEB_PORT, HF_TOKEN, XDG_CACHE_HOME (written), WHISPER_MODEL, BATCH_WHISPER_MODE, BATCH_CONDITION_ON_PREVIOUS_TEXT, NUM_SPEAKERS, DISCOURSE_ENABLED/MODEL/EFFORT/MAX_TOKENS, ANTHROPIC_API_KEY, LIVE_ENGINE, LIVE_MODEL_QUANT, LIVE_MODEL_PATH, LIVE_MOONSHINE_MODEL_DIR, LIVE_MOONSHINE_CHUNK_SECONDS, LIVE_LANGUAGE, LIVE_WHISPER_THREADS, LIVE_VAD_*, LIVE_MIN/MAX_UTTERANCE_*, LIVE_PREROLL_MS, LIVE_PARTIAL_*, LIVE_KEYWORD_LIMIT, LIVE_GRAPH_* (7), LIVE_DISCONNECT_FINALIZE_SECONDS, TERMS_EXTRA_STOPWORDS.

## 9. Packaging blockers & risks
**Blocking:**
1. `__file__`-relative templates dir (app.py:25) — needs datas + _MEIPASS-aware path.
2. `BASE_DIR="."` CWD-dependent at import — inject TRANSCRIBE_BASE_DIR.
3. Bare `ffmpeg` on PATH (+ whisper internal calls) — ship ffmpeg, prepend PATH.
4. WS Origin check rejects tauri.localhost (close 1008).
5. `getDisplayMedia` system audio likely fails in WebView2; mic getUserMedia OK (secure context) but needs WebView2 PermissionRequested handling.
6. Google Fonts CDN — vendor fonts locally.
7. Fixed port 8000, no fallback — need free-port probe / port-0 + handshake.

**Attention:**
8. PyInstaller hidden imports: uvicorn string-imports, transformers dynamic loading, pyannote/speechbrain/lightning config-string classes, numba/llvmlite binaries, whisper assets (mel_filters.npz, multilingual.tiktoken), silero_vad data.
9. XDG_CACHE_HOME mid-flight mutation / split cache roots.
10. Worker daemon thread `while True: sleep(30)`, no signal handlers anywhere; Windows sidecar kill = hard terminate → orphaned live WAV in tmp_audio. Consider graceful-shutdown IPC.
11. `timeout_graceful_shutdown=5` only shutdown tuning.
12. `threading.Timer` 60s auto-finalize on disconnect (session.py:248-253).
13. print() everywhere → sidecar stdout/stderr pipes must be drained (Windows pipe-full blocking).
14. ffmpeg console window flash (no CREATE_NO_WINDOW).
15. No multiprocessing (good — no freeze_support trap). All threads.
16. No weights bundled; first run downloads 0.1–3GB; batch needs user HF_TOKEN (gated pyannote); Moonshine ja license restricts redistribution.
