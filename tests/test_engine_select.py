"""Unit tests for LIVE_ENGINE selection in _create_engine (no torch/deps).

The CUDA probe, weight-presence checks and both engine constructors are
patched with sentinels so every branch is exercised without building real
engines or importing transformers/pywhispercpp.
"""
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.live import engine as engine_module  # noqa: E402
from src.live import engine_moonshine as moonshine_module  # noqa: E402

WHISPER_SENTINEL = object()
MOONSHINE_SENTINEL = object()


@contextmanager
def _patched(*, cuda: bool = False, moonshine: bool = False, ggml: bool = False):
    saved = (
        engine_module.LiveEngine,
        moonshine_module.MoonshineEngine,
        engine_module._cuda_available,
        engine_module._moonshine_weights_present,
        engine_module._ggml_weights_present,
    )
    engine_module.LiveEngine = lambda *args, **kwargs: WHISPER_SENTINEL
    moonshine_module.MoonshineEngine = lambda *args, **kwargs: MOONSHINE_SENTINEL
    engine_module._cuda_available = lambda: cuda
    engine_module._moonshine_weights_present = lambda: moonshine
    engine_module._ggml_weights_present = lambda: ggml
    try:
        yield
    finally:
        (
            engine_module.LiveEngine,
            moonshine_module.MoonshineEngine,
            engine_module._cuda_available,
            engine_module._moonshine_weights_present,
            engine_module._ggml_weights_present,
        ) = saved


def _create(choice: str, **flags):
    with _patched(**flags):
        return engine_module._create_engine(choice)


def test_explicit_whispercpp():
    assert _create("whispercpp") is WHISPER_SENTINEL


def test_explicit_moonshine():
    assert _create("moonshine") is MOONSHINE_SENTINEL


def test_choice_is_normalised():
    assert _create("  WhisperCPP ") is WHISPER_SENTINEL
    assert _create("MOONSHINE") is MOONSHINE_SENTINEL


def test_auto_prefers_whispercpp_on_cuda():
    # GPU hosts keep the existing whisper.cpp path even with moonshine weights.
    assert _create("auto", cuda=True, moonshine=True, ggml=True) is WHISPER_SENTINEL


def test_auto_selects_moonshine_without_cuda():
    assert _create("auto", cuda=False, moonshine=True, ggml=True) is MOONSHINE_SENTINEL


def test_auto_falls_back_to_whispercpp_cpu():
    assert _create("auto", cuda=False, moonshine=False, ggml=True) is WHISPER_SENTINEL


def test_auto_without_any_weights_raises():
    try:
        _create("auto", cuda=False, moonshine=False, ggml=False)
    except engine_module.LiveEngineError as error:
        message = str(error)
        assert "fetch_live_model.py" in message
        assert "fetch_moonshine_model.py" in message
        return
    raise AssertionError("expected LiveEngineError")


def test_empty_choice_defaults_to_moonshine():
    assert _create("") is MOONSHINE_SENTINEL


def test_invalid_choice_raises():
    try:
        _create("bogus")
    except engine_module.LiveEngineError as error:
        assert "LIVE_ENGINE" in str(error)
        return
    raise AssertionError("expected LiveEngineError")


def test_get_engine_is_singleton():
    # Default LIVE_ENGINE is moonshine (CPU-first).
    with _patched():
        saved = engine_module._engine
        engine_module._engine = None
        try:
            first = engine_module.get_engine()
            second = engine_module.get_engine()
            assert first is MOONSHINE_SENTINEL
            assert first is second
        finally:
            engine_module._engine = saved


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
