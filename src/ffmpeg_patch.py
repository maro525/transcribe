"""ffmpeg resolution + Windows console-window suppression (desktop packaging).

The desktop (Tauri) shell ships its own ffmpeg binary and points the backend
at it via the ``FFMPEG_PATH`` env var (absolute path to the executable).
This module centralises how every ffmpeg invocation resolves the binary:

- ``resolve_ffmpeg()``: FFMPEG_PATH when set, else the bare ``"ffmpeg"``
  (PATH lookup) — dev usage without the env var is unchanged.
- ``creation_flags()``: ``subprocess.CREATE_NO_WINDOW`` on Windows so no
  console window flashes for each spawn; ``0`` elsewhere (passing 0 is a
  no-op on POSIX).
- ``apply()``: called once from ``main.py``. Prepends FFMPEG_PATH's directory
  to ``PATH`` (covers any library that shells out to a bare ``ffmpeg``) and
  wraps openai-whisper's internal ffmpeg invocation (``whisper.audio.run``)
  so it uses the resolved binary and CREATE_NO_WINDOW too. The patch is
  defensive: if whisper is not installed, ``apply()`` is a silent no-op.

Stdlib-only; whisper is imported lazily inside ``apply()``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_FFMPEG_ENV_VAR = "FFMPEG_PATH"
_DEFAULT_FFMPEG = "ffmpeg"
_PATCH_MARKER = "_transcribe_ffmpeg_patched"

_applied = False


def _is_windows() -> bool:
    return sys.platform == "win32"


def resolve_ffmpeg() -> str:
    """The ffmpeg executable to spawn: FFMPEG_PATH, else bare ``ffmpeg``."""
    configured = (os.environ.get(_FFMPEG_ENV_VAR) or "").strip()
    return configured or _DEFAULT_FFMPEG


def creation_flags() -> int:
    """Windows-only subprocess creationflags to suppress the console window.

    getattr-guarded: ``CREATE_NO_WINDOW`` only exists on Windows builds of
    the subprocess module. Returns 0 elsewhere, which callers can always
    pass through unchanged.
    """
    if not _is_windows():
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def apply() -> None:
    """Install the process-wide ffmpeg adjustments (idempotent)."""
    global _applied
    if _applied:
        return
    _applied = True
    _prepend_ffmpeg_dir_to_path()
    _patch_whisper_ffmpeg()


def _prepend_ffmpeg_dir_to_path() -> None:
    """Make FFMPEG_PATH's directory win any bare-name ``ffmpeg`` lookup."""
    configured = (os.environ.get(_FFMPEG_ENV_VAR) or "").strip()
    if not configured:
        return
    ffmpeg_dir = str(Path(configured).parent)
    current = os.environ.get("PATH", "")
    if current.split(os.pathsep)[0] == ffmpeg_dir:
        return
    os.environ["PATH"] = ffmpeg_dir + os.pathsep + current if current else ffmpeg_dir


def _patch_whisper_ffmpeg() -> None:
    """Wrap ``whisper.audio.run`` (whisper's ffmpeg spawn) in place.

    openai-whisper's ``load_audio`` shells out via a module-local ``run``
    (``from subprocess import run``) with a bare ``"ffmpeg"`` argv[0]. The
    wrapper substitutes the resolved binary and adds CREATE_NO_WINDOW on
    Windows. Only needed when FFMPEG_PATH is set or on Windows; skipped —
    without failing — when whisper is absent (live-only deployments).
    """
    if not os.environ.get(_FFMPEG_ENV_VAR) and not _is_windows():
        return
    try:
        import whisper.audio as whisper_audio
    except Exception:
        return
    original_run = getattr(whisper_audio, "run", None)
    if original_run is None or getattr(original_run, _PATCH_MARKER, False):
        return

    def patched_run(cmd, *args, **kwargs):  # signature mirrors subprocess.run
        if cmd and cmd[0] == _DEFAULT_FFMPEG:
            cmd = [resolve_ffmpeg(), *cmd[1:]]
        flags = creation_flags()
        if flags:
            kwargs.setdefault("creationflags", flags)
        return original_run(cmd, *args, **kwargs)

    setattr(patched_run, _PATCH_MARKER, True)
    whisper_audio.run = patched_run
