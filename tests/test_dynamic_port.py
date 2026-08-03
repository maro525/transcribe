"""Tests for the TRANSCRIBE_DYNAMIC_PORT=1 handshake mode (tauri-desktop A1).

Covers the TAURI_READY line format, env-flag parsing, the uvicorn wiring of
``_serve_dynamic_port`` (faked server), and one real end-to-end run: uvicorn
on a port-0 socket answering /healthz and exiting via POST /internal/shutdown.

Requires fastapi + uvicorn (+ httpx for the end-to-end test); SKIPs cleanly
in the bare environment. main.py imports the ML worker lazily, so importing
it here needs no torch/whisper.
"""
import io
import json
import os
import socket
import sys
import threading
import time
import types
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import httpx  # noqa: F401
    import uvicorn  # noqa: F401
    from fastapi import FastAPI  # noqa: F401

    HAVE_WEB_STACK = True
except ImportError:
    HAVE_WEB_STACK = False

if HAVE_WEB_STACK:
    import main as main_module
    import src.web.app as app_module
    from src.version import BACKEND_VERSION, PROTOCOL_VERSION


def _skip(name: str) -> bool:
    if not HAVE_WEB_STACK:
        print(f"SKIP {name}: fastapi/uvicorn/httpx not installed")
        return True
    return False


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


def test_dynamic_port_flag_parsing():
    if _skip("test_dynamic_port_flag_parsing"):
        return
    with _env(TRANSCRIBE_DYNAMIC_PORT=None):
        assert not main_module._dynamic_port_enabled()
    for off_value in ("0", "", "true", "yes"):
        with _env(TRANSCRIBE_DYNAMIC_PORT=off_value):
            assert not main_module._dynamic_port_enabled()
    with _env(TRANSCRIBE_DYNAMIC_PORT="1"):
        assert main_module._dynamic_port_enabled()


def test_tauri_ready_line_format():
    if _skip("test_tauri_ready_line_format"):
        return
    line = main_module._tauri_ready_line(43210)
    assert "\n" not in line
    prefix = "TAURI_READY "
    assert line.startswith(prefix)
    payload = json.loads(line[len(prefix):])
    assert payload == {
        "port": 43210,
        "protocol": PROTOCOL_VERSION,
        "backend_version": BACKEND_VERSION,
    }
    assert payload["protocol"] == 1
    assert isinstance(payload["backend_version"], str)


def test_serve_dynamic_port_wiring_with_fake_server():
    if _skip("test_serve_dynamic_port_wiring_with_fake_server"):
        return

    class FakeConfig:
        def __init__(self, app, **kwargs):
            self.app = app
            self.kwargs = kwargs

    class FakeServer:
        def __init__(self, config):
            self.config = config
            self.served_sockets = None
            self.should_exit = False

        async def serve(self, sockets=None):
            self.served_sockets = sockets

    fake_uvicorn = types.SimpleNamespace(Config=FakeConfig, Server=FakeServer)
    app = types.SimpleNamespace(state=types.SimpleNamespace())
    saved = main_module.uvicorn
    main_module.uvicorn = fake_uvicorn
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            main_module._serve_dynamic_port(app)
    finally:
        main_module.uvicorn = saved

    server = app.state.uvicorn_server
    assert isinstance(server, FakeServer)
    assert server.config.app is app
    sockets = server.served_sockets
    assert sockets is not None and len(sockets) == 1
    host, port = sockets[0].getsockname()[:2]
    assert port > 0
    # handshake line announces exactly the bound port, before serve()
    lines = [
        line
        for line in buffer.getvalue().splitlines()
        if line.startswith("TAURI_READY ")
    ]
    assert len(lines) == 1
    payload = json.loads(lines[0][len("TAURI_READY "):])
    assert payload["port"] == port
    sockets[0].close()


def test_end_to_end_dynamic_port_healthz_and_shutdown():
    """Real uvicorn on a port-0 socket: TAURI_READY -> /healthz -> shutdown."""
    if _skip("test_end_to_end_dynamic_port_healthz_and_shutdown"):
        return
    import httpx

    buffer = io.StringIO()
    with _env(TRANSCRIBE_SHUTDOWN_SECRET="e2e-secret"):
        app = app_module.create_app()

    def run_server():
        with redirect_stdout(buffer):
            main_module._serve_dynamic_port(app)

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    # wait for the handshake line
    deadline = time.monotonic() + 15
    port = None
    while time.monotonic() < deadline and port is None:
        for line in buffer.getvalue().splitlines():
            if line.startswith("TAURI_READY "):
                port = json.loads(line[len("TAURI_READY "):])["port"]
        time.sleep(0.05)
    assert port, "TAURI_READY line never appeared"

    base_url = f"http://127.0.0.1:{port}"
    body = None
    while time.monotonic() < deadline:
        try:
            body = httpx.get(f"{base_url}/healthz", timeout=2).json()
            break
        except Exception:
            time.sleep(0.1)
    assert body is not None, "/healthz never became reachable"
    assert body["status"] == "ok"
    assert body["protocol"] == 1
    assert body["backend_version"] == BACKEND_VERSION

    # wrong token rejected, correct token drains the server
    denied = httpx.post(
        f"{base_url}/internal/shutdown",
        headers={"X-Shutdown-Token": "nope"},
        timeout=2,
    )
    assert denied.status_code == 403
    accepted = httpx.post(
        f"{base_url}/internal/shutdown",
        headers={"X-Shutdown-Token": "e2e-secret"},
        timeout=2,
    )
    assert accepted.status_code == 202
    thread.join(timeout=15)
    assert not thread.is_alive(), "server did not exit after shutdown"


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
