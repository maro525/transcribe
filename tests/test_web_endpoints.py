"""Endpoint tests for /healthz, /internal/shutdown, /internal/models/moonshine
and the /static mount (tauri-desktop A2/A5/A9).

Requires fastapi + httpx (TestClient). In the bare unit-test environment
(numpy only) every test SKIPs instead of failing, keeping
``python3 tests/test_web_endpoints.py`` green everywhere.
"""
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from fastapi.testclient import TestClient

    HAVE_FASTAPI = True
except ImportError:
    HAVE_FASTAPI = False

if HAVE_FASTAPI:
    import src.web.app as app_module
    from src import worker_state
    from src.live import moonshine_fetch
    from src.version import BACKEND_VERSION, PROTOCOL_VERSION


def _skip(name: str) -> bool:
    if not HAVE_FASTAPI:
        print(f"SKIP {name}: fastapi/httpx not installed")
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


def _client(**env) -> "TestClient":
    with _env(**env):
        app = app_module.create_app()
    return TestClient(app)


# --- /healthz ---------------------------------------------------------------

def test_healthz_contract():
    if _skip("test_healthz_contract"):
        return
    response = _client().get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["protocol"] == PROTOCOL_VERSION == 1
    assert body["backend_version"] == BACKEND_VERSION
    assert body["worker"] in ("loading", "ready", "failed")


def test_healthz_reflects_worker_state():
    if _skip("test_healthz_reflects_worker_state"):
        return
    client = _client()
    try:
        for state in ("loading", "ready", "failed"):
            worker_state.set_state(state)
            assert client.get("/healthz").json()["worker"] == state
    finally:
        worker_state.set_state(worker_state.WORKER_LOADING)


# --- POST /internal/shutdown -------------------------------------------------

def test_shutdown_is_404_without_configured_secret():
    if _skip("test_shutdown_is_404_without_configured_secret"):
        return
    client = _client(TRANSCRIBE_SHUTDOWN_SECRET=None)
    response = client.post(
        "/internal/shutdown", headers={"X-Shutdown-Token": "anything"}
    )
    assert response.status_code == 404


def test_shutdown_rejects_wrong_or_missing_token():
    if _skip("test_shutdown_rejects_wrong_or_missing_token"):
        return
    client = _client(TRANSCRIBE_SHUTDOWN_SECRET="s3cret")
    assert client.post("/internal/shutdown").status_code == 403
    response = client.post(
        "/internal/shutdown", headers={"X-Shutdown-Token": "wrong"}
    )
    assert response.status_code == 403


def test_shutdown_with_valid_token_sets_should_exit():
    if _skip("test_shutdown_with_valid_token_sets_should_exit"):
        return

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
    if _skip("test_shutdown_finalizes_recording_live_session"):
        return

    class FakeManager:
        def __init__(self):
            self.stopped = False

        def status(self):
            return {"state": "recording"}

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


# --- /internal/models/moonshine ----------------------------------------------

def test_moonshine_post_without_consent_is_403_with_license_link():
    if _skip("test_moonshine_post_without_consent_is_403_with_license_link"):
        return
    client = _client()
    for payload in (None, {}, {"accept_license": False}, {"accept_license": "yes"}):
        response = client.post("/internal/models/moonshine", json=payload)
        assert response.status_code == 403
        assert "moonshine.ai/community-license" in response.json()["detail"]


def test_moonshine_post_with_consent_records_and_starts_download():
    if _skip("test_moonshine_post_with_consent_records_and_starts_download"):
        return
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
            "/internal/models/moonshine", json={"accept_license": True}
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
    if _skip("test_moonshine_get_returns_status"):
        return
    body = _client().get("/internal/models/moonshine").json()
    assert body["status"] in ("idle", "downloading", "done", "failed")
    assert "license_accepted" in body
    assert "weights_present" in body


# --- /static -----------------------------------------------------------------

def test_static_fonts_are_served():
    if _skip("test_static_fonts_are_served"):
        return
    client = _client()
    css = client.get("/static/fonts/fonts.css")
    assert css.status_code == 200
    assert "@font-face" in css.text
    assert "url(https://" not in css.text.replace("'", "")  # fully local srcs
    woff2 = client.get("/static/fonts/inter-latin.woff2")
    assert woff2.status_code == 200
    assert woff2.content[:4] == b"wOF2"


def test_templates_no_longer_reference_font_cdn():
    if _skip("test_templates_no_longer_reference_font_cdn"):
        return
    templates_dir = Path(app_module.TEMPLATES_DIR)
    for name in ("index.html", "detail.html", "live.html"):
        html = (templates_dir / name).read_text(encoding="utf-8")
        assert "fonts.googleapis.com" not in html, name
        assert "fonts.gstatic.com" not in html, name


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
