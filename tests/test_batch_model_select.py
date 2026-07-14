"""Unit tests for batch WHISPER_MODEL resolution (no torch/deps).

resolve_whisper_model is a pure function in src.config: given the raw explicit
model, the mode string and a cuda_available flag it returns a frozen
ResolvedWhisperModel. These tests import only src.config — no torch, no GPU, no
model downloads (mirrors tests/test_engine_select.py).

Note: test_default_mode_constant_is_light reads the import-time value, so the
suite must run with BATCH_WHISPER_MODE unset in the environment (normal case).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402
from src.config import resolve_whisper_model  # noqa: E402


def _resolve(explicit, mode, cuda):
    return resolve_whisper_model(
        explicit_model=explicit, mode=mode, cuda_available=cuda
    )


# --- explicit WHISPER_MODEL always wins -------------------------------------

def test_explicit_wins_over_strong_on_cuda():
    assert _resolve("small", "strong", True).name == "small"


def test_explicit_wins_over_max_on_cuda():
    assert _resolve("small", "max", True).name == "small"


def test_explicit_wins_on_cpu():
    assert _resolve("large-v3", "light", False).name == "large-v3"


def test_explicit_wins_over_invalid_mode():
    # explicit set => mode is never validated (no ValueError).
    assert _resolve("small", "bogus", True).name == "small"


def test_explicit_is_stripped():
    assert _resolve(" medium ", "light", True).name == "medium"


# --- mode-driven resolution (WHISPER_MODEL unset) ---------------------------

def test_unset_everything_is_medium():
    assert _resolve(None, "light", False).name == config.BATCH_MODEL_LIGHT
    assert _resolve(None, "light", True).name == config.BATCH_MODEL_LIGHT


def test_empty_explicit_and_mode_default_to_medium():
    assert _resolve("", None, False).name == config.BATCH_MODEL_LIGHT


def test_whitespace_mode_defaults_to_medium():
    assert _resolve(None, "   ", False).name == config.BATCH_MODEL_LIGHT


def test_strong_on_cuda_is_turbo():
    assert _resolve(None, "strong", True).name == config.BATCH_MODEL_STRONG


def test_strong_on_cpu_degrades_to_medium():
    result = _resolve(None, "strong", False)
    assert result.name == config.BATCH_MODEL_LIGHT
    assert "CUDA" in result.reason


def test_max_on_cuda_is_large_v3():
    assert _resolve(None, "max", True).name == config.BATCH_MODEL_MAX


def test_max_on_cpu_degrades_to_medium():
    result = _resolve(None, "max", False)
    assert result.name == config.BATCH_MODEL_LIGHT
    assert "CUDA" in result.reason


def test_mode_is_normalised():
    assert _resolve(None, "  STRONG ", True).name == config.BATCH_MODEL_STRONG


def test_invalid_mode_raises():
    try:
        _resolve(None, "bogus", True)
    except ValueError as error:
        assert "BATCH_WHISPER_MODE" in str(error)
        return
    raise AssertionError("expected ValueError")


# --- module default ---------------------------------------------------------

def test_default_mode_constant_is_light():
    assert config.BATCH_WHISPER_MODE == "light"


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
