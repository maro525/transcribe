"""WS tests for the native PCM feeder path (tauri-desktop A8).

The desktop shell captures system audio in Rust and connects to /live/ws as
a SECOND WebSocket client (no Origin header, binary frames only). These
tests assert that:
- a browser-less client (no Origin header) passes the CSWSH check,
- binary frames from the feeder client land in the SAME session that the
  control client started,
- a cross-origin browser connection is still rejected (1008).

Requires fastapi + httpx; SKIPs cleanly in the bare environment.
"""
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from fastapi.testclient import TestClient

    HAVE_FASTAPI = True
except ImportError:
    HAVE_FASTAPI = False

import numpy as np  # noqa: E402

if HAVE_FASTAPI:
    import src.web.app as app_module
    from src.live.session import LiveSessionManager

SR = 16000


def _skip(name: str) -> bool:
    if not HAVE_FASTAPI:
        print(f"SKIP {name}: fastapi/httpx not installed")
        return True
    return False


class FakeVad:
    def probability(self, frame) -> float:
        return 1.0 if float(np.abs(frame).max()) > 0.1 else 0.0

    def reset(self) -> None:
        pass


class FakeEngine:
    def transcribe_partial(self, audio):
        return "partial"

    def transcribe_final(self, audio):
        return "final text"


def _pcm_bytes(value: float, seconds: float) -> bytes:
    samples = np.full(int(SR * seconds), int(value * 32767), dtype=np.int16)
    return samples.tobytes()


class _patched_manager:
    """Swap the app's module-level live_manager for an isolated one."""

    def __init__(self, base: Path):
        self.manager = LiveSessionManager(
            source_dir=base / "input",
            output_dir=base / "output",
            temp_dir=base / "tmp",
            engine_provider=FakeEngine,
            vad_factory=FakeVad,
            disconnect_finalize_seconds=60.0,
        )

    def __enter__(self):
        self._saved = app_module.live_manager
        app_module.live_manager = self.manager
        return self.manager

    def __exit__(self, *exc):
        app_module.live_manager = self._saved
        return False


def _wait(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def test_feeder_without_origin_header_is_accepted_and_feeds_same_session(tmp_path=None):
    if _skip("test_feeder_without_origin_header_is_accepted_and_feeds_same_session"):
        return
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with _patched_manager(base) as manager:
            client = TestClient(app_module.create_app())
            # Client 1: browser control path (start/stop + own PCM).
            # TestClient sends no Origin header by default — same trust model
            # as the Rust feeder; the check must allow both.
            with client.websocket_connect("/live/ws") as control:
                assert control.receive_json()["type"] == "status"  # replay
                control.send_json({"type": "start", "source": "system"})
                _wait(lambda: manager.status()["state"] == "recording")
                session_id = manager.status()["session_id"]

                # Client 2: the native feeder — binary frames only.
                with client.websocket_connect("/live/ws") as feeder:
                    assert feeder.receive_json()["type"] == "status"
                    feeder.send_bytes(_pcm_bytes(0.5, 1.0))
                    _wait(lambda: manager.status()["elapsed_sec"] >= 1.0)
                    # same session, still recording
                    assert manager.status()["session_id"] == session_id

                    # control client's own bytes land in the same session too
                    control.send_bytes(_pcm_bytes(0.5, 1.0))
                    _wait(lambda: manager.status()["elapsed_sec"] >= 2.0)

                control.send_json({"type": "stop"})
                _wait(lambda: manager.status()["state"] == "idle")

            wavs = list((base / "input").glob("meeting_*.wav"))
            assert len(wavs) == 1
            with wave.open(str(wavs[0]), "rb") as handle:
                # both clients' PCM was recorded into one WAV
                assert handle.getnframes() == 2 * SR


def test_cross_origin_browser_is_still_rejected():
    if _skip("test_cross_origin_browser_is_still_rejected"):
        return
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        with _patched_manager(Path(tmp)):
            client = TestClient(app_module.create_app())
            try:
                with client.websocket_connect(
                    "/live/ws", headers={"Origin": "http://evil.example"}
                ) as websocket:
                    message = websocket.receive()
                    assert message["type"] == "websocket.close"
                    assert message.get("code") == 1008
            except Exception as error:  # some stacks raise on close-before-accept
                assert "1008" in str(error) or "WebSocketDisconnect" in type(error).__name__


def test_same_origin_browser_is_accepted():
    if _skip("test_same_origin_browser_is_accepted"):
        return
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        with _patched_manager(Path(tmp)):
            client = TestClient(app_module.create_app())
            # TestClient's Host is "testserver"; a matching Origin must pass.
            with client.websocket_connect(
                "/live/ws", headers={"Origin": "http://testserver"}
            ) as websocket:
                assert websocket.receive_json()["type"] == "status"


def test_feeder_disconnect_does_not_kill_session_while_control_connected():
    """The 60 s auto-finalize arms only when the LAST client drops. A feeder
    (Rust) disconnect while the browser stays connected must not finalize."""
    if _skip("test_feeder_disconnect_does_not_kill_session_while_control_connected"):
        return
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with _patched_manager(base) as manager:
            client = TestClient(app_module.create_app())
            with client.websocket_connect("/live/ws") as control:
                control.receive_json()
                control.send_json({"type": "start", "source": "system"})
                _wait(lambda: manager.status()["state"] == "recording")
                with client.websocket_connect("/live/ws") as feeder:
                    feeder.receive_json()
                    feeder.send_bytes(_pcm_bytes(0.5, 1.0))
                    _wait(lambda: manager.status()["elapsed_sec"] >= 1.0)
                # feeder gone; control still connected
                time.sleep(0.2)
                assert manager.status()["state"] == "recording"
                assert manager._disconnect_timer is None  # not armed
                control.send_json({"type": "stop"})
                _wait(lambda: manager.status()["state"] == "idle")


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
