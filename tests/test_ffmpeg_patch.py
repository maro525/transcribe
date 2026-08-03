"""Unit tests for src/ffmpeg_patch.py (stdlib-only, mocked subprocess).

Windows-specific behaviour (CREATE_NO_WINDOW actually suppressing the
console) cannot be verified on this host; these tests only assert that the
flag is passed through when the platform reports win32.
"""
import os
import subprocess
import sys
import tempfile
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import audio, ffmpeg_patch  # noqa: E402

FAKE_CREATE_NO_WINDOW = 0x08000000  # value of the real Windows constant


@contextmanager
def _fake_windows():
    """Pretend to run on win32: platform probe + subprocess constant."""
    had_attr = hasattr(subprocess, "CREATE_NO_WINDOW")
    if not had_attr:
        subprocess.CREATE_NO_WINDOW = FAKE_CREATE_NO_WINDOW
    saved_is_windows = ffmpeg_patch._is_windows
    ffmpeg_patch._is_windows = lambda: True
    try:
        yield
    finally:
        ffmpeg_patch._is_windows = saved_is_windows
        if not had_attr:
            del subprocess.CREATE_NO_WINDOW


@contextmanager
def _env(**values):
    saved = {key: os.environ.get(key) for key in values}
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _fresh_patch_state():
    saved = ffmpeg_patch._applied
    ffmpeg_patch._applied = False
    try:
        yield
    finally:
        ffmpeg_patch._applied = saved


def test_resolve_ffmpeg_defaults_to_bare_name():
    with _env(FFMPEG_PATH=None):
        assert ffmpeg_patch.resolve_ffmpeg() == "ffmpeg"


def test_resolve_ffmpeg_prefers_env_var():
    with _env(FFMPEG_PATH="/opt/ffmpeg/bin/ffmpeg.exe"):
        assert ffmpeg_patch.resolve_ffmpeg() == "/opt/ffmpeg/bin/ffmpeg.exe"


def test_resolve_ffmpeg_ignores_blank_env_var():
    with _env(FFMPEG_PATH="   "):
        assert ffmpeg_patch.resolve_ffmpeg() == "ffmpeg"


def test_creation_flags_zero_off_windows():
    assert ffmpeg_patch.creation_flags() == 0


def test_creation_flags_no_window_on_windows():
    with _fake_windows():
        assert ffmpeg_patch.creation_flags() == FAKE_CREATE_NO_WINDOW


def test_ensure_wav_passes_resolved_binary_and_flags():
    with tempfile.TemporaryDirectory() as tmp:
        temp_dir = Path(tmp)
        with _env(FFMPEG_PATH="/custom/ffmpeg"), _fake_windows():
            with patch("subprocess.run") as mocked_run:
                wav_path, created = audio.ensure_wav(
                    temp_dir / "meeting.mp3", temp_dir
                )
        assert created
        assert wav_path == temp_dir / "meeting.wav"
        (args,), kwargs = mocked_run.call_args
        assert args[0] == "/custom/ffmpeg"
        assert kwargs["creationflags"] == FAKE_CREATE_NO_WINDOW


def test_ensure_wav_default_invocation_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        temp_dir = Path(tmp)
        with _env(FFMPEG_PATH=None):
            with patch("subprocess.run") as mocked_run:
                audio.ensure_wav(temp_dir / "meeting.m4a", temp_dir)
        (args,), kwargs = mocked_run.call_args
        assert args[0] == "ffmpeg"
        assert kwargs["creationflags"] == 0


def test_ensure_wav_short_circuits_existing_wav():
    with tempfile.TemporaryDirectory() as tmp:
        temp_dir = Path(tmp)
        with patch("subprocess.run") as mocked_run:
            wav_path, created = audio.ensure_wav(temp_dir / "a.wav", temp_dir)
        assert not created
        assert wav_path == temp_dir / "a.wav"
        mocked_run.assert_not_called()


def test_apply_prepends_ffmpeg_dir_to_path():
    with _fresh_patch_state():
        with _env(FFMPEG_PATH="/bundle/bin/ffmpeg.exe", PATH="/usr/bin"):
            ffmpeg_patch.apply()
            assert os.environ["PATH"].split(os.pathsep)[0] == "/bundle/bin"


def test_apply_is_idempotent_for_path():
    with _fresh_patch_state():
        with _env(FFMPEG_PATH="/bundle/bin/ffmpeg.exe", PATH="/usr/bin"):
            ffmpeg_patch.apply()
            first = os.environ["PATH"]
            ffmpeg_patch._applied = False  # force a second pass
            ffmpeg_patch.apply()
            assert os.environ["PATH"] == first


def test_apply_without_env_var_leaves_path_untouched():
    with _fresh_patch_state():
        with _env(FFMPEG_PATH=None, PATH="/usr/bin"):
            ffmpeg_patch.apply()
            assert os.environ["PATH"] == "/usr/bin"


def test_apply_survives_missing_whisper():
    # whisper is not installed in the unit-test environment: apply() must not
    # raise even when the patch path is triggered (FFMPEG_PATH set).
    with _fresh_patch_state():
        with _env(FFMPEG_PATH="/bundle/bin/ffmpeg"):
            ffmpeg_patch.apply()  # must not raise


def _install_fake_whisper():
    """A minimal whisper.audio with a recording ``run`` (subprocess-style)."""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append((list(cmd), kwargs))
        return types.SimpleNamespace(stdout=b"")

    whisper_module = types.ModuleType("whisper")
    audio_module = types.ModuleType("whisper.audio")
    audio_module.run = fake_run
    whisper_module.audio = audio_module
    sys.modules["whisper"] = whisper_module
    sys.modules["whisper.audio"] = audio_module
    return audio_module, calls


def _remove_fake_whisper():
    sys.modules.pop("whisper", None)
    sys.modules.pop("whisper.audio", None)


def test_whisper_run_is_wrapped_with_resolved_binary_and_flags():
    audio_module, calls = _install_fake_whisper()
    try:
        with _fresh_patch_state():
            with _env(FFMPEG_PATH="/bundle/bin/ffmpeg.exe"), _fake_windows():
                ffmpeg_patch.apply()
                audio_module.run(
                    ["ffmpeg", "-nostdin", "-i", "x.mp3"], capture_output=True
                )
        (cmd, kwargs) = calls[-1]
        assert cmd[0] == "/bundle/bin/ffmpeg.exe"
        assert cmd[1:] == ["-nostdin", "-i", "x.mp3"]
        assert kwargs["creationflags"] == FAKE_CREATE_NO_WINDOW
        assert kwargs["capture_output"] is True
    finally:
        _remove_fake_whisper()


def test_whisper_wrap_is_idempotent():
    audio_module, calls = _install_fake_whisper()
    try:
        with _fresh_patch_state():
            with _env(FFMPEG_PATH="/bundle/bin/ffmpeg"):
                ffmpeg_patch.apply()
                once = audio_module.run
                ffmpeg_patch._applied = False
                ffmpeg_patch.apply()
                assert audio_module.run is once
    finally:
        _remove_fake_whisper()


def test_whisper_non_ffmpeg_commands_pass_through():
    audio_module, calls = _install_fake_whisper()
    try:
        with _fresh_patch_state():
            with _env(FFMPEG_PATH="/bundle/bin/ffmpeg"):
                ffmpeg_patch.apply()
                audio_module.run(["other-tool", "--version"])
        (cmd, _kwargs) = calls[-1]
        assert cmd == ["other-tool", "--version"]
    finally:
        _remove_fake_whisper()


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
