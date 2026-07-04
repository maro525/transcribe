"""Process-wide flag: is a live transcription session active?

Kept in its own tiny module so `watcher.py` / `worker.py` can depend on it
without importing any live-mode machinery (torch, whisper.cpp, ...).
"""
from __future__ import annotations

import threading

_live_active = threading.Event()


def is_live_active() -> bool:
    return _live_active.is_set()


def set_live_active() -> None:
    _live_active.set()


def clear_live_active() -> None:
    _live_active.clear()
