"""Unit tests for moonshine chunk splitting / token budget (no transformers)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Importing the module itself proves it stays import-safe without
# transformers/torch installed (lazy-import requirement).
from src.live.engine import LiveEngineError  # noqa: E402
from src.live.engine_moonshine import (  # noqa: E402
    MAX_CHUNK_SECONDS,
    TOKENS_PER_SEC,
    MoonshineEngine,
    chunk_spans,
    token_budget,
)

SAMPLE_RATE = 16000
CHUNK = 12 * SAMPLE_RATE  # LIVE_MOONSHINE_CHUNK_SECONDS default, in samples


def test_empty_input_yields_no_spans():
    assert chunk_spans(0, CHUNK) == []
    assert chunk_spans(-1, CHUNK) == []


def test_short_input_is_single_span():
    assert chunk_spans(SAMPLE_RATE, CHUNK) == [(0, SAMPLE_RATE)]


def test_exact_chunk_is_single_span():
    assert chunk_spans(CHUNK, CHUNK) == [(0, CHUNK)]


def test_one_sample_over_splits_into_two():
    assert chunk_spans(CHUNK + 1, CHUNK) == [(0, CHUNK), (CHUNK, CHUNK + 1)]


def test_30s_splits_into_three_contiguous_spans():
    total = 30 * SAMPLE_RATE
    spans = chunk_spans(total, CHUNK)
    assert len(spans) == 3
    assert spans[0][0] == 0
    assert spans[-1][1] == total
    assert all(end - start <= CHUNK for start, end in spans)
    # Every sample covered exactly once (contiguous, no overlap).
    assert all(spans[i][1] == spans[i + 1][0] for i in range(len(spans) - 1))


def test_chunk_samples_must_be_positive():
    for bad in (0, -CHUNK):
        try:
            chunk_spans(SAMPLE_RATE, bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for chunk_samples={bad}")


def test_token_budget_uses_model_card_rate():
    assert TOKENS_PER_SEC == 13
    assert token_budget(12.0) == 156  # 12 s * 13 tokens/s
    assert token_budget(1.0) == 13


def test_token_budget_floors_at_one():
    assert token_budget(0.0) == 1
    assert token_budget(0.01) == 1


def test_token_budget_stays_under_decoder_cap_for_chunk():
    # A 12 s chunk (and anything below the ~14.9 s ceiling) must stay under
    # the moonshine decoder's 194-token maximum.
    assert token_budget(12.0) < 194
    assert token_budget(14.9) <= 194
    assert token_budget(MAX_CHUNK_SECONDS) <= 194


def test_engine_rejects_out_of_range_chunk_seconds():
    # Validation fires before the weights check, so it is testable without
    # model files or transformers installed.
    for bad in (0.0, -1.0, MAX_CHUNK_SECONDS + 0.1, 30.0):
        try:
            MoonshineEngine(chunk_seconds=bad)
        except LiveEngineError as error:
            assert "LIVE_MOONSHINE_CHUNK_SECONDS" in str(error)
            continue
        raise AssertionError(f"expected LiveEngineError for chunk_seconds={bad}")


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
