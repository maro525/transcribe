"""Endpoint tests for /healthz, /internal/shutdown, /internal/models/moonshine,
the host allowlist, the /live template and the /static mount (tauri-desktop
A2/A5/A9 + review round fixes).

Requires fastapi + httpx (TestClient). In the bare unit-test environment
(numpy only) the whole module SKIPs instead of failing, keeping both
``pytest`` and ``python3 tests/test_web_endpoints.py`` green everywhere.
"""
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from fastapi.testclient import TestClient
except ImportError:
    if __name__ == "__main__":
        print("SKIP tests/test_web_endpoints.py: fastapi/httpx not installed")
        raise SystemExit(0)
    import pytest

    pytest.skip("fastapi/httpx not installed", allow_module_level=True)

import src.web.app as app_module  # noqa: E402
from src import worker_state  # noqa: E402
from src.live import moonshine_fetch  # noqa: E402
from src.live.session import LiveSessionError  # noqa: E402
from src.version import BACKEND_VERSION, PROTOCOL_VERSION  # noqa: E402

INTERNAL_HEADERS = {"X-Transcribe-Internal": "1"}


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


def _client(**env) -> "TestClient":
    with _env(**env):
        app = app_module.create_app()
    # base_url picks the Host header: must pass the DNS-rebinding allowlist.
    return TestClient(app, base_url="http://127.0.0.1")


# --- /healthz ---------------------------------------------------------------

def test_healthz_contract():
    response = _client().get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["protocol"] == PROTOCOL_VERSION == 1
    assert body["backend_version"] == BACKEND_VERSION
    assert body["worker"] in ("loading", "ready", "failed")


def test_healthz_reflects_worker_state():
    client = _client()
    try:
        for state in ("loading", "ready", "failed"):
            worker_state.set_state(state)
            assert client.get("/healthz").json()["worker"] == state
    finally:
        worker_state.set_state(worker_state.WORKER_LOADING)


# --- Host allowlist (DNS-rebinding defense) ----------------------------------

def test_unknown_host_header_is_403():
    client = _client()
    for host in ("evil.example", "evil.example:8000", "testserver"):
        response = client.get("/healthz", headers={"Host": host})
        assert response.status_code == 403, host


def test_loopback_host_aliases_are_allowed():
    client = _client()
    for host in ("127.0.0.1", "127.0.0.1:8000", "localhost", "localhost:9",
                 "[::1]:8000"):
        response = client.get("/healthz", headers={"Host": host})
        assert response.status_code == 200, host


# --- POST /internal/shutdown -------------------------------------------------

def test_shutdown_is_404_without_configured_secret():
    client = _client(TRANSCRIBE_SHUTDOWN_SECRET=None)
    response = client.post(
        "/internal/shutdown", headers={"X-Shutdown-Token": "anything"}
    )
    assert response.status_code == 404


def test_shutdown_rejects_wrong_or_missing_token():
    client = _client(TRANSCRIBE_SHUTDOWN_SECRET="s3cret")
    assert client.post("/internal/shutdown").status_code == 403
    response = client.post(
        "/internal/shutdown", headers={"X-Shutdown-Token": "wrong"}
    )
    assert response.status_code == 403


def test_shutdown_with_valid_token_sets_should_exit():
    class FakeServer:
        should_exit = False

    client = _client(TRANSCRIBE_SHUTDOWN_SECRET="s3cret")
    server = FakeServer()
    client.app.state.uvicorn_server = server
    response = client.post(
        "/internal/shutdown", headers={"X-Shutdown-Token": "s3cret"}
    )
    assert response.status_code == 202
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not server.should_exit:
        time.sleep(0.02)
    assert server.should_exit


def test_shutdown_finalizes_recording_live_session():
    class FakeManager:
        def __init__(self):
            self.stopped = False

        def status(self):
            return {"state": "idle" if self.stopped else "recording"}

        def stop(self):
            self.stopped = True

    class FakeServer:
        should_exit = False

    fake_manager = FakeManager()
    saved = app_module.live_manager
    app_module.live_manager = fake_manager
    try:
        client = _client(TRANSCRIBE_SHUTDOWN_SECRET="s3cret")
        server = FakeServer()
        client.app.state.uvicorn_server = server
        response = client.post(
            "/internal/shutdown", headers={"X-Shutdown-Token": "s3cret"}
        )
        assert response.status_code == 202
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not server.should_exit:
            time.sleep(0.02)
        assert fake_manager.stopped  # finalize happened before should_exit
        assert server.should_exit
    finally:
        app_module.live_manager = saved


def test_shutdown_waits_for_inflight_finalize():
    """A session already in "finalizing" (e.g. the auto-finalize timer fired)
    must complete before should_exit is set."""

    class FakeManager:
        def __init__(self):
            self.idle_at = time.monotonic() + 0.6

        def status(self):
            state = "idle" if time.monotonic() >= self.idle_at else "finalizing"
            return {"state": state}

        def stop(self):  # pragma: no cover - must not be called
            raise AssertionError("stop() must not be called while finalizing")

    class FakeServer:
        should_exit = False

    fake_manager = FakeManager()
    saved = app_module.live_manager
    app_module.live_manager = fake_manager
    try:
        client = _client(TRANSCRIBE_SHUTDOWN_SECRET="s3cret")
        server = FakeServer()
        client.app.state.uvicorn_server = server
        response = client.post(
            "/internal/shutdown", headers={"X-Shutdown-Token": "s3cret"}
        )
        assert response.status_code == 202
        # while the finalize is in flight, the server must keep running
        time.sleep(0.2)
        assert not server.should_exit
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not server.should_exit:
            time.sleep(0.02)
        assert server.should_exit
        assert time.monotonic() >= fake_manager.idle_at
    finally:
        app_module.live_manager = saved


def test_shutdown_survives_stop_race_with_auto_finalize():
    """stop() may raise LiveSessionError when the auto-finalize timer wins
    the race; shutdown must still complete (and wait out the finalize)."""

    class FakeManager:
        def __init__(self):
            self.state = "recording"

        def status(self):
            return {"state": self.state}

        def stop(self):
            self.state = "idle"  # the racing finalize already completed
            raise LiveSessionError("no live session is recording")

    class FakeServer:
        should_exit = False

    fake_manager = FakeManager()
    saved = app_module.live_manager
    app_module.live_manager = fake_manager
    try:
        client = _client(TRANSCRIBE_SHUTDOWN_SECRET="s3cret")
        server = FakeServer()
        client.app.state.uvicorn_server = server
        assert (
            client.post(
                "/internal/shutdown", headers={"X-Shutdown-Token": "s3cret"}
            ).status_code
            == 202
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not server.should_exit:
            time.sleep(0.02)
        assert server.should_exit
    finally:
        app_module.live_manager = saved


# --- /internal/models/moonshine ----------------------------------------------

def test_moonshine_post_without_internal_header_is_403():
    client = _client()
    response = client.post(
        "/internal/models/moonshine", json={"accept_license": True}
    )
    assert response.status_code == 403
    # INTERNAL_REQUEST_HEADER is lowercase ("x-transcribe-internal"); the
    # detail message echoes it verbatim, so match case-insensitively.
    assert "x-transcribe-internal" in response.json()["detail"].lower()


def test_moonshine_post_malformed_json_is_400():
    client = _client()
    for content in (b"", b"{not json", b"[1,2"):
        response = client.post(
            "/internal/models/moonshine",
            content=content,
            headers={**INTERNAL_HEADERS, "Content-Type": "application/json"},
        )
        assert response.status_code == 400, content


def test_moonshine_post_without_consent_is_403_with_license_link():
    client = _client()
    for payload in ({}, {"accept_license": False}, {"accept_license": "yes"}):
        response = client.post(
            "/internal/models/moonshine", json=payload, headers=INTERNAL_HEADERS
        )
        assert response.status_code == 403
        assert "moonshine.ai/community-license" in response.json()["detail"]


def test_moonshine_post_with_consent_records_and_starts_download():
    calls = {"recorded": 0, "started": 0}
    saved = (
        moonshine_fetch.record_license_acceptance,
        moonshine_fetch.start_background_download,
    )
    moonshine_fetch.record_license_acceptance = (
        lambda *a, **k: calls.__setitem__("recorded", calls["recorded"] + 1) or {}
    )
    moonshine_fetch.start_background_download = (
        lambda *a, **k: calls.__setitem__("started", calls["started"] + 1) or True
    )
    try:
        client = _client()
        response = client.post(
            "/internal/models/moonshine",
            json={"accept_license": True},
            headers=INTERNAL_HEADERS,
        )
        assert response.status_code == 202
        body = response.json()
        assert body["started"] is True
        assert calls == {"recorded": 1, "started": 1}
    finally:
        (
            moonshine_fetch.record_license_acceptance,
            moonshine_fetch.start_background_download,
        ) = saved


def test_moonshine_get_returns_status():
    body = _client().get("/internal/models/moonshine").json()
    assert body["status"] in ("idle", "downloading", "done", "failed")
    assert "license_accepted" in body
    assert "weights_present" in body


def test_moonshine_thread_start_failure_marks_status_failed():
    """If the download thread cannot start, status must not stay stuck at
    "downloading" (review follow-up)."""
    import threading as threading_module

    class BrokenThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("no threads left")

    saved_thread = moonshine_fetch.threading.Thread
    saved_status = moonshine_fetch._status
    saved_error = moonshine_fetch._error
    moonshine_fetch.threading.Thread = BrokenThread
    try:
        moonshine_fetch._status = moonshine_fetch.DOWNLOAD_IDLE
        moonshine_fetch._error = None
        try:
            moonshine_fetch.start_background_download()
            raise AssertionError("start_background_download must re-raise")
        except RuntimeError:
            pass
        assert moonshine_fetch.download_status()["status"] == "failed"
        assert "no threads left" in moonshine_fetch.download_status()["error"]
    finally:
        moonshine_fetch.threading.Thread = saved_thread
        moonshine_fetch._status = saved_status
        moonshine_fetch._error = saved_error
        assert moonshine_fetch.threading.Thread is threading_module.Thread


# --- /live template (desktop_mode flag) ---------------------------------------

def test_live_system_option_disabled_in_plain_browser_mode():
    html = _client(TRANSCRIBE_DYNAMIC_PORT=None).get("/live").text
    option = next(
        line for line in html.splitlines() if 'value="system"' in line
    )
    assert "disabled" in option
    assert "デスクトップアプリ版でのみ利用できます" in option


def test_live_system_option_enabled_in_desktop_mode():
    html = _client(TRANSCRIBE_DYNAMIC_PORT="1").get("/live").text
    option = next(
        line for line in html.splitlines() if 'value="system"' in line
    )
    assert "disabled" not in option


# --- /static -----------------------------------------------------------------

def test_static_fonts_are_served():
    client = _client()
    css = client.get("/static/fonts/fonts.css")
    assert css.status_code == 200
    assert "@font-face" in css.text
    assert "url(https://" not in css.text.replace("'", "")  # fully local srcs
    woff2 = client.get("/static/fonts/inter-latin.woff2")
    assert woff2.status_code == 200
    assert woff2.content[:4] == b"wOF2"


def test_templates_no_longer_reference_font_cdn():
    templates_dir = Path(app_module.TEMPLATES_DIR)
    for name in ("index.html", "detail.html", "live.html"):
        html = (templates_dir / name).read_text(encoding="utf-8")
        assert "fonts.googleapis.com" not in html, name
        assert "fonts.gstatic.com" not in html, name


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
