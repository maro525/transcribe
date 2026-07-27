"""Process-wide batch-worker readiness state: loading -> ready | failed.

Mirrors ``src/live/state.py``: a tiny stdlib-only module so the web layer
(GET /healthz) can report worker readiness without importing any of the
batch-model machinery (torch, whisper, pyannote). ``worker.run`` moves the
state to ``ready`` once all models are loaded, or to ``failed`` when startup
raises.
"""
from __future__ import annotations

import threading

WORKER_LOADING = "loading"
WORKER_READY = "ready"
WORKER_FAILED = "failed"

_VALID_STATES = (WORKER_LOADING, WORKER_READY, WORKER_FAILED)

_lock = threading.Lock()
_state = WORKER_LOADING


def get_state() -> str:
    with _lock:
        return _state


def set_state(state: str) -> None:
    if state not in _VALID_STATES:
        raise ValueError(
            f"invalid worker state {state!r} (expected one of {_VALID_STATES})"
        )
    global _state
    with _lock:
        _state = state
