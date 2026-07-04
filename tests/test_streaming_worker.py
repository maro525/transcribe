"""Unit tests for the transcription worker (fake engine, real thread)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from src.live.streaming import TranscriptionWorker  # noqa: E402

SR = 16000


class FakeEngine:
    def __init__(self):
        self.partial_calls = []
        self.final_calls = []

    def transcribe_partial(self, audio):
        self.partial_calls.append(audio.size)
        return f"partial-{audio.size}"

    def transcribe_final(self, audio):
        self.final_calls.append(audio.size)
        return f"final-{audio.size}"


def _worker(engine, messages, **kwargs):
    defaults = dict(
        partial_interval=0.0,  # no rate limiting in tests
        partial_window_seconds=2.0,
        sample_rate=SR,
    )
    defaults.update(kwargs)
    return TranscriptionWorker(lambda: engine, messages.append, **defaults)


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_update_then_end_emits_partial_then_final():
    engine = FakeEngine()
    messages = []
    worker = _worker(engine, messages)
    worker.start()
    audio = np.zeros(SR, dtype=np.float32)

    worker.on_utterance_update(1, audio, t0=0.0)
    assert _wait_for(lambda: any(m["type"] == "partial" for m in messages))

    worker.on_utterance_end(1, audio, t0=0.0, t1=1.0)
    worker.stop()
    finals = [m for m in messages if m["type"] == "final"]
    assert len(finals) == 1
    assert finals[0]["utterance_id"] == 1
    assert finals[0]["text"] == f"final-{SR}"
    assert finals[0]["t1"] == 1.0


def test_stale_partial_is_skipped_after_final():
    engine = FakeEngine()
    messages = []
    worker = _worker(engine, messages)
    audio = np.zeros(SR, dtype=np.float32)
    # enqueue a partial, then finalize BEFORE starting the thread:
    # the queued partial must be skipped in favour of the final.
    worker.on_utterance_update(1, audio, t0=0.0)
    worker.on_utterance_end(1, audio, t0=0.0, t1=1.0)
    worker.start()
    worker.stop()

    assert engine.partial_calls == []
    assert [m["type"] for m in messages if m["type"] != "error"] == ["final"]


def test_cancel_emits_empty_final():
    engine = FakeEngine()
    messages = []
    worker = _worker(engine, messages)
    worker.start()
    worker.on_utterance_cancel(3, t0=2.5)
    worker.stop()

    finals = [m for m in messages if m["type"] == "final"]
    assert len(finals) == 1
    assert finals[0]["utterance_id"] == 3
    assert finals[0]["text"] == ""
    assert engine.final_calls == []


def test_partial_window_freezes_prefix_text():
    engine = FakeEngine()
    messages = []
    worker = _worker(engine, messages, partial_window_seconds=1.0)
    worker.start()

    first = np.zeros(SR // 2, dtype=np.float32)  # 0.5s — within window
    worker.on_utterance_update(1, first, t0=0.0)
    assert _wait_for(lambda: len([m for m in messages if m["type"] == "partial"]) == 1)

    grown = np.zeros(3 * SR, dtype=np.float32)  # 3s — beyond the 1s window
    worker.on_utterance_update(1, grown, t0=0.0)
    assert _wait_for(lambda: len([m for m in messages if m["type"] == "partial"]) == 2)
    worker.stop()

    partials = [m for m in messages if m["type"] == "partial"]
    # 2nd partial re-inferred only the audio after the 1st partial's coverage
    assert engine.partial_calls[1] == 3 * SR - SR // 2
    # ...and its text carries the frozen prefix from the 1st partial
    assert partials[1]["text"].startswith(partials[0]["text"])


def test_partial_interval_rate_limits_requests():
    engine = FakeEngine()
    messages = []
    worker = _worker(engine, messages, partial_interval=60.0)
    worker.start()
    audio = np.zeros(SR, dtype=np.float32)
    worker.on_utterance_update(1, audio, t0=0.0)
    worker.on_utterance_update(1, audio, t0=0.0)
    worker.on_utterance_update(1, audio, t0=0.0)
    worker.stop()

    partials = [m for m in messages if m["type"] == "partial"]
    assert len(partials) == 1


def test_engine_error_emits_error_and_worker_survives():
    class BrokenEngine(FakeEngine):
        def transcribe_final(self, audio):
            raise RuntimeError("boom")

    engine = BrokenEngine()
    messages = []
    worker = _worker(engine, messages)
    worker.start()
    audio = np.zeros(SR, dtype=np.float32)
    worker.on_utterance_end(1, audio, t0=0.0, t1=1.0)
    assert _wait_for(lambda: any(m["type"] == "error" for m in messages))
    # the worker thread is still alive and can process further jobs
    worker.on_utterance_cancel(2, t0=1.0)
    worker.stop()
    assert any(m["type"] == "final" and m["utterance_id"] == 2 for m in messages)


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
