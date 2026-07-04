"""Unit tests for the utterance segmentation state machine (no torch)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from src.live.vad import FRAME_SIZE, UtteranceSegmenter  # noqa: E402

_FRAME_MS = FRAME_SIZE * 1000 / 16000  # 32 ms


def _energy_prob(frame: np.ndarray) -> float:
    """Synthetic VAD: loud frames are speech."""
    return 1.0 if float(np.abs(frame).max()) > 0.1 else 0.0


def _speech(frames: int) -> np.ndarray:
    return np.full(frames * FRAME_SIZE, 0.5, dtype=np.float32)


def _silence(frames: int) -> np.ndarray:
    return np.zeros(frames * FRAME_SIZE, dtype=np.float32)


def _segmenter(**overrides) -> UtteranceSegmenter:
    defaults = dict(
        threshold=0.5,
        min_silence_ms=160,  # 5 frames
        min_utterance_ms=96,  # 3 frames
        max_utterance_seconds=1.0,
        preroll_ms=64,  # 2 frames
    )
    defaults.update(overrides)
    return UtteranceSegmenter(_energy_prob, **defaults)


def _kinds(events) -> list[str]:
    return [event.kind for event in events]


def test_speech_then_silence_emits_start_update_end():
    seg = _segmenter()
    events = seg.process(_speech(10))
    assert _kinds(events)[0] == "start"
    assert "update" in _kinds(events)

    events = seg.process(_silence(6))
    kinds = _kinds(events)
    assert "end" in kinds
    end = next(e for e in events if e.kind == "end")
    assert end.utterance_id == 1
    assert end.t1 is not None and end.t1 > end.t0
    assert end.audio.size > 0


def test_short_noise_burst_is_cancelled():
    seg = _segmenter()
    seg.process(_speech(1))  # 1 frame of speech < min 3 frames
    events = seg.process(_silence(6))
    assert "cancel" in _kinds(events)
    assert "end" not in _kinds(events)


def test_preroll_is_prepended_to_utterance_audio():
    seg = _segmenter()
    seg.process(_silence(4))  # fills the preroll deque
    events = seg.process(_speech(1))
    start = next(e for e in events if e.kind == "start")
    # preroll (2 frames) + current frame = 3 frames of audio
    assert start.audio.size == 3 * FRAME_SIZE


def test_max_utterance_forces_finalize():
    seg = _segmenter(max_utterance_seconds=0.32)  # 10 frames
    events = seg.process(_speech(20))
    kinds = _kinds(events)
    assert "end" in kinds
    # a second utterance starts after the forced split
    end_index = kinds.index("end")
    assert "start" in kinds[end_index + 1:]
    ids = {e.utterance_id for e in events}
    assert ids == {1, 2}


def test_utterance_ids_increase_across_utterances():
    seg = _segmenter()
    seg.process(_speech(5))
    events1 = seg.process(_silence(6))
    end1 = next(e for e in events1 if e.kind == "end")
    seg.process(_speech(5))
    events2 = seg.process(_silence(6))
    end2 = next(e for e in events2 if e.kind == "end")
    assert end2.utterance_id == end1.utterance_id + 1
    assert end2.t0 >= end1.t1


def test_flush_finalizes_in_progress_utterance():
    seg = _segmenter()
    seg.process(_speech(5))
    events = seg.flush()
    assert _kinds(events) == ["end"]
    assert seg.flush() == []


def test_partial_chunks_are_buffered_across_calls():
    seg = _segmenter()
    half = FRAME_SIZE // 2
    events = seg.process(np.full(half, 0.5, dtype=np.float32))
    assert events == []  # not enough for one frame yet
    events = seg.process(np.full(half, 0.5, dtype=np.float32))
    assert "start" in _kinds(events)


def test_silence_only_never_starts_utterance():
    seg = _segmenter()
    assert seg.process(_silence(50)) == []
    assert seg.flush() == []


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
