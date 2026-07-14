# Current Project: NSKETCH-883 — Batch strong/accuracy mode (openai-whisper)

## Goal
- GPU hosts opt into stronger batch models via BATCH_WHISPER_MODE (strong=large-v3-turbo, max=large-v3); CPU hosts keep medium. WHISPER_MODEL explicit override always wins.

## Key files
- `src/config.py` — raw env reads + pure `resolve_whisper_model()` (stdlib-only, torch-free).
- `src/worker.py` — startup resolution, `ready (device, whisper, reason)` log.
- `tests/test_batch_model_select.py` — pure resolver matrix, no torch/GPU/downloads.

## Architecture
- Resolution at worker startup (not config import); CUDA passed as parameter to keep the resolver pure.
- Mirrors the live-engine selection pattern (`src/live/engine.py` / `tests/test_engine_select.py`).

## Decisions
- New env `BATCH_WHISPER_MODE=light|strong|max` (default light); no `WHISPER_MODEL=auto` sentinel.
- CPU + strong/max → fall back to medium with logged reason; never force heavy on CPU.
- Invalid mode → ValueError at startup (fail-fast, matches LIVE_ENGINE handling).
- Scope: batch only; openai-whisper stays the engine; #2 ASR-first architecture excluded.
