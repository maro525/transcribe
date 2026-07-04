"""Integration-style tests for LiveSessionManager (fake VAD + fake engine)."""
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from src.live import state as live_state  # noqa: E402
from src.live.session import LiveSessionError, LiveSessionManager  # noqa: E402

SR = 16000


class FakeVad:
    """Energy-based stand-in for Silero VAD."""

    def probability(self, frame: np.ndarray) -> float:
        return 1.0 if float(np.abs(frame).max()) > 0.1 else 0.0

    def reset(self) -> None:
        pass


class FakeEngine:
    def transcribe_partial(self, audio):
        return "とちゅう"

    def transcribe_final(self, audio):
        return "これはテスト発話です"


def _pcm_bytes(value: float, seconds: float) -> bytes:
    samples = np.full(int(SR * seconds), int(value * 32767), dtype=np.int16)
    return samples.tobytes()


def _manager(base: Path) -> LiveSessionManager:
    return LiveSessionManager(
        source_dir=base / "input",
        output_dir=base / "output",
        temp_dir=base / "tmp",
        engine_provider=FakeEngine,
        vad_factory=FakeVad,
        disconnect_finalize_seconds=0.2,
    )


def test_full_session_produces_wav_draft_and_history():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        manager = _manager(base)
        messages = []
        manager.add_listener(messages.append)

        manager.start(source="mic")
        assert live_state.is_live_active()
        assert manager.status()["state"] == "recording"

        manager.feed_pcm(_pcm_bytes(0.5, 2.0))   # speech
        manager.feed_pcm(_pcm_bytes(0.0, 1.0))   # silence -> utterance end
        manager.stop()

        assert not live_state.is_live_active()
        assert manager.status()["state"] == "idle"

        # WAV was handed over to input/ with the meeting_ prefix
        wavs = list((base / "input").glob("meeting_*.wav"))
        assert len(wavs) == 1
        with wave.open(str(wavs[0]), "rb") as handle:
            assert handle.getframerate() == SR
            assert handle.getnchannels() == 1
            assert handle.getnframes() == 3 * SR  # everything fed was recorded

        # live draft holds the final text
        drafts = list((base / "output").glob("meeting_*_live_draft.txt"))
        assert len(drafts) == 1
        assert "これはテスト発話です" in drafts[0].read_text(encoding="utf-8")

        types = [m["type"] for m in messages]
        assert "final" in types
        assert "keywords" in types
        assert "finalized" in types
        finalized = next(m for m in messages if m["type"] == "finalized")
        assert finalized["wav"] == str(wavs[0])
        assert finalized["draft"] == str(drafts[0])


def test_second_start_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        manager = _manager(Path(tmp))
        manager.start()
        try:
            try:
                manager.start()
                raise AssertionError("second start must raise")
            except LiveSessionError:
                pass
        finally:
            manager.stop()


def test_stop_without_recording_raises():
    with tempfile.TemporaryDirectory() as tmp:
        manager = _manager(Path(tmp))
        try:
            manager.stop()
            raise AssertionError("stop while idle must raise")
        except LiveSessionError:
            pass


def test_replay_sends_status_and_final_history():
    with tempfile.TemporaryDirectory() as tmp:
        manager = _manager(Path(tmp))
        manager.start()
        manager.feed_pcm(_pcm_bytes(0.5, 2.0))
        manager.feed_pcm(_pcm_bytes(0.0, 1.0))

        import time

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            replayed = []
            manager.replay(replayed.append)
            if any(m["type"] == "final" for m in replayed):
                break
            time.sleep(0.05)

        assert replayed[0]["type"] == "status"
        assert replayed[0]["state"] == "recording"
        assert any(m["type"] == "final" for m in replayed)
        assert any(m["type"] == "keywords" for m in replayed)
        manager.stop()


def test_disconnect_auto_finalizes_after_grace_period():
    import time

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        manager = _manager(base)
        manager.client_connected()
        manager.start()
        manager.feed_pcm(_pcm_bytes(0.5, 1.0))
        manager.client_disconnected()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if manager.status()["state"] == "idle":
                break
            time.sleep(0.05)
        assert manager.status()["state"] == "idle"
        assert list((base / "input").glob("meeting_*.wav"))
        assert not live_state.is_live_active()


def test_reconnect_cancels_auto_finalize():
    import time

    with tempfile.TemporaryDirectory() as tmp:
        manager = _manager(Path(tmp))
        manager.client_connected()
        manager.start()
        manager.client_disconnected()
        manager.client_connected()  # reconnect within the grace period
        time.sleep(0.5)
        assert manager.status()["state"] == "recording"
        manager.stop()


def test_pause_flag_follows_session_lifecycle():
    with tempfile.TemporaryDirectory() as tmp:
        manager = _manager(Path(tmp))
        assert not live_state.is_live_active()
        manager.start()
        assert live_state.is_live_active()
        manager.stop()
        assert not live_state.is_live_active()


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
