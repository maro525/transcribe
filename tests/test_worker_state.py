"""Unit tests for src/worker_state.py (stdlib only)."""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import worker_state  # noqa: E402


def _reset():
    worker_state.set_state(worker_state.WORKER_LOADING)


def test_initial_state_is_loading():
    _reset()
    assert worker_state.get_state() == "loading"


def test_transitions_ready_and_failed():
    _reset()
    worker_state.set_state(worker_state.WORKER_READY)
    assert worker_state.get_state() == "ready"
    worker_state.set_state(worker_state.WORKER_FAILED)
    assert worker_state.get_state() == "failed"
    _reset()


def test_invalid_state_raises():
    _reset()
    try:
        worker_state.set_state("bogus")
    except ValueError as error:
        assert "bogus" in str(error)
    else:
        raise AssertionError("expected ValueError")
    assert worker_state.get_state() == "loading"


def test_contract_values_match_healthz_spec():
    # /healthz contract (frozen): worker is "loading"|"ready"|"failed"
    assert worker_state.WORKER_LOADING == "loading"
    assert worker_state.WORKER_READY == "ready"
    assert worker_state.WORKER_FAILED == "failed"


def test_concurrent_reads_and_writes_do_not_corrupt():
    _reset()
    stop = threading.Event()
    seen = set()

    def writer():
        while not stop.is_set():
            worker_state.set_state(worker_state.WORKER_READY)
            worker_state.set_state(worker_state.WORKER_LOADING)

    def reader():
        while not stop.is_set():
            seen.add(worker_state.get_state())

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    import time

    time.sleep(0.2)
    stop.set()
    for thread in threads:
        thread.join(timeout=2)
    assert seen <= {"loading", "ready"}
    _reset()


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
