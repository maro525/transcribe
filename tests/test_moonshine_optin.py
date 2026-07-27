"""Unit tests for src/live/moonshine_fetch.py (mocked huggingface_hub)."""
import json
import sys
import tempfile
import time
import types
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.live import moonshine_fetch  # noqa: E402


@contextmanager
def _fake_hub(snapshot_download):
    module = types.ModuleType("huggingface_hub")
    module.snapshot_download = snapshot_download
    saved = sys.modules.get("huggingface_hub")
    sys.modules["huggingface_hub"] = module
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop("huggingface_hub", None)
        else:
            sys.modules["huggingface_hub"] = saved


@contextmanager
def _fresh_download_state():
    with moonshine_fetch._lock:
        saved = (moonshine_fetch._status, moonshine_fetch._error)
        moonshine_fetch._status = moonshine_fetch.DOWNLOAD_IDLE
        moonshine_fetch._error = None
    try:
        yield
    finally:
        with moonshine_fetch._lock:
            moonshine_fetch._status, moonshine_fetch._error = saved


def _wait_for_status(expected: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if moonshine_fetch.download_status()["status"] == expected:
            return
        time.sleep(0.02)
    raise AssertionError(
        f"status never became {expected}: {moonshine_fetch.download_status()}"
    )


def test_license_not_accepted_by_default():
    with tempfile.TemporaryDirectory() as tmp:
        assert not moonshine_fetch.license_accepted(Path(tmp) / "nope.json")


def test_record_and_check_license_acceptance():
    with tempfile.TemporaryDirectory() as tmp:
        record_path = Path(tmp) / "moonshine_license.json"
        record = moonshine_fetch.record_license_acceptance(record_path)
        assert record["accepted_at"]
        assert record["license_url"] == "https://moonshine.ai/community-license"
        assert record["repo_id"] == moonshine_fetch.HF_REPO_ID
        assert moonshine_fetch.license_accepted(record_path)
        # persisted file round-trips
        on_disk = json.loads(record_path.read_text(encoding="utf-8"))
        assert on_disk == record


def test_corrupt_license_record_counts_as_not_accepted():
    with tempfile.TemporaryDirectory() as tmp:
        record_path = Path(tmp) / "moonshine_license.json"
        record_path.write_text("{not json", encoding="utf-8")
        assert not moonshine_fetch.license_accepted(record_path)


def test_download_calls_snapshot_download_with_repo_and_dest():
    calls = []

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        return kwargs["local_dir"]

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "moonshine-tiny-ja"
        with _fake_hub(fake_snapshot_download):
            result = moonshine_fetch.download_moonshine_model(dest)
        assert result == dest
        assert dest.is_dir()  # created before the fetch
        assert calls[0]["repo_id"] == "UsefulSensors/moonshine-tiny-ja"
        assert calls[0]["local_dir"] == str(dest)
        assert "*.safetensors" in calls[0]["allow_patterns"]


def test_background_download_reaches_done():
    with tempfile.TemporaryDirectory() as tmp, _fresh_download_state():
        with _fake_hub(lambda **kwargs: kwargs["local_dir"]):
            assert moonshine_fetch.start_background_download(Path(tmp)) is True
            _wait_for_status("done")
        status = moonshine_fetch.download_status()
        assert status["error"] is None


def test_background_download_failure_is_reported():
    def broken(**kwargs):
        raise RuntimeError("network down")

    with tempfile.TemporaryDirectory() as tmp, _fresh_download_state():
        with _fake_hub(broken):
            assert moonshine_fetch.start_background_download(Path(tmp)) is True
            _wait_for_status("failed")
        status = moonshine_fetch.download_status()
        assert "network down" in status["error"]


def test_second_start_while_running_is_rejected():
    import threading

    release = threading.Event()

    def slow(**kwargs):
        release.wait(5)
        return kwargs["local_dir"]

    with tempfile.TemporaryDirectory() as tmp, _fresh_download_state():
        with _fake_hub(slow):
            assert moonshine_fetch.start_background_download(Path(tmp)) is True
            assert moonshine_fetch.start_background_download(Path(tmp)) is False
            release.set()
            _wait_for_status("done")


def test_status_shape():
    with _fresh_download_state():
        status = moonshine_fetch.download_status()
        assert set(status) == {
            "status",
            "error",
            "dest",
            "license_accepted",
            "weights_present",
        }
        assert status["status"] == "idle"


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
