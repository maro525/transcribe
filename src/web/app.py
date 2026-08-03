import asyncio
import hmac
import json
import os
import threading
import time
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from pathlib import Path
from queue import Empty
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlsplit

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import config, worker_state
from ..artifacts import (
    GraphEditsError,
    GraphRevisionConflict,
    StructureEditsError,
    StructureRevisionConflict,
    graph_edits_body_too_large,
    read_limited_graph_edits_payload,
    load_graph,
    load_keywords,
    load_structure,
    update_graph_edits,
    update_structure_edits,
)
from ..live import moonshine_fetch
from ..live.session import LiveSessionError, manager as live_manager
from ..status import JobState, StoreEvent, store
from ..version import BACKEND_VERSION, PROTOCOL_VERSION

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

LifespanFactory = Callable[[FastAPI], AbstractAsyncContextManager]
HEARTBEAT_SECONDS = 15
SSE_RETRY_MS = 2000
LIVE_WS_OUTBOUND_MAXSIZE = 1000

# Desktop (Tauri) shell sends this header on POST /internal/shutdown; the
# expected value arrives via the TRANSCRIBE_SHUTDOWN_SECRET env var.
SHUTDOWN_TOKEN_HEADER = "x-shutdown-token"
SHUTDOWN_SECRET_ENV_VAR = "TRANSCRIBE_SHUTDOWN_SECRET"

# Feeder-role WS connections (no Origin header — the Rust native-capture
# client) authenticate with this header; the expected value is the same
# TRANSCRIBE_SHUTDOWN_SECRET (frozen contract with the Tauri shell).
FEEDER_TOKEN_HEADER = "x-feeder-token"

# POST /internal/models/moonshine requires this custom header. Custom headers
# force a CORS preflight on cross-origin requests; with no CORS middleware
# the preflight fails, so cross-origin simple requests are impossible.
INTERNAL_REQUEST_HEADER = "x-transcribe-internal"

# Set to "1" by the desktop (Tauri) shell (same env var main.py reads for
# dynamic-port mode); gates desktop-only UI such as native system capture.
DYNAMIC_PORT_ENV_VAR = "TRANSCRIBE_DYNAMIC_PORT"

# _graceful_shutdown: how long to wait for an in-flight finalize (worker
# drain is capped at 60 s in session.stop()) before exiting anyway.
FINALIZE_WAIT_SECONDS = 65.0
FINALIZE_POLL_SECONDS = 0.2


def _host_allowed(host_header: str) -> bool:
    """DNS-rebinding defense: the Host header must name loopback (or the
    configured bind host). Applies to every HTTP request and WS handshake."""
    try:
        hostname = urlsplit(f"//{host_header}").hostname
    except ValueError:
        return False
    return hostname in {"127.0.0.1", "localhost", "::1", config.WEB_HOST}


def _origin_allowed(websocket: WebSocket) -> bool:
    """Reject cross-site WebSocket connections (CSWSH defense).

    Browsers always send Origin on WS handshakes; non-browser clients
    (no Origin header) take the feeder path in ``live_ws`` instead.
    """
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    host = websocket.headers.get("host", "")
    origin_host = urlsplit(origin).netloc
    return bool(host) and origin_host == host


def _reject_traversal(filename: str) -> None:
    """404 any path-like filename before it reaches the filesystem (D6)."""
    if Path(filename).name != filename:
        raise HTTPException(status_code=404)


async def _read_limited_graph_edits_body(request: Request) -> bytes:
    """Read only up to the graph edit limit from a streamed request body."""
    try:
        return await read_limited_graph_edits_payload(request.stream())
    except ValueError:
        raise HTTPException(status_code=413, detail="edit request is too large") from None


def format_sse(
    event: str,
    data: dict[str, Any],
    event_id: int | None = None,
    retry: int | None = None,
) -> str:
    lines: list[str] = []
    if retry is not None:
        lines.append(f"retry: {retry}")
    lines.append(f"event: {event}")
    if event_id is not None:
        lines.append(f"id: {event_id}")
    payload = json.dumps(data, ensure_ascii=False)
    lines.extend(f"data: {line}" for line in payload.splitlines())
    return "\n".join(lines) + "\n\n"


async def event_stream(request: Request) -> AsyncIterator[str]:
    subscriber, snapshot, snapshot_version = store.subscribe_with_snapshot()
    try:
        yield format_sse(
            "snapshot", snapshot, event_id=snapshot_version, retry=SSE_RETRY_MS
        )
        while not await request.is_disconnected():
            try:
                event = await asyncio.to_thread(
                    subscriber.get, True, HEARTBEAT_SECONDS
                )
            except Empty:
                yield format_sse(
                    "heartbeat",
                    {"ts": datetime.now().isoformat(timespec="seconds")},
                )
                continue

            if isinstance(event, StoreEvent) and event.id > snapshot_version:
                yield format_sse(event.event, event.data, event_id=event.id)
    finally:
        store.unsubscribe(subscriber)


def _graceful_shutdown(app: FastAPI) -> None:
    """Finalize a recording live session, then ask uvicorn to exit.

    Runs on a daemon thread so POST /internal/shutdown can return 202
    immediately (a live finalize can take up to a minute). The uvicorn
    server instance is stored on ``app.state`` by main.py in dynamic-port
    (Tauri) mode; when absent (plain dev run) only the finalize happens.
    """
    try:
        if live_manager.status()["state"] == "recording":
            try:
                live_manager.stop()
            except LiveSessionError:
                pass  # lost a race with the auto-finalize timer
        # An auto-finalize (or another stop) may already be mid-flight:
        # give it time to hand the WAV to input/ before exiting. This runs
        # on a daemon thread, so the sleep never blocks the event loop.
        deadline = time.monotonic() + FINALIZE_WAIT_SECONDS
        while (
            live_manager.status()["state"] == "finalizing"
            and time.monotonic() < deadline
        ):
            time.sleep(FINALIZE_POLL_SECONDS)
    except Exception as error:
        print(f"Shutdown: live session finalize failed: {error}")
    server = getattr(app.state, "uvicorn_server", None)
    if server is not None:
        server.should_exit = True


def create_app(lifespan: LifespanFactory | None = None) -> FastAPI:
    app = FastAPI(title="Transcribe Dashboard", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    # Read at app-creation time (not import time) so tests and the desktop
    # shell can control it; None disables the shutdown endpoint entirely.
    app.state.shutdown_secret = os.environ.get(SHUTDOWN_SECRET_ENV_VAR) or None
    app.state.uvicorn_server = None
    app.state.desktop_mode = os.environ.get(DYNAMIC_PORT_ENV_VAR) == "1"

    @app.middleware("http")
    async def host_allowlist(request: Request, call_next):
        if not _host_allowed(request.headers.get("host", "")):
            return JSONResponse({"detail": "invalid host header"}, status_code=403)
        return await call_next(request)

    @app.get("/healthz")
    def healthz():
        return JSONResponse(
            {
                "status": "ok",
                "protocol": PROTOCOL_VERSION,
                "backend_version": BACKEND_VERSION,
                "worker": worker_state.get_state(),
            }
        )

    @app.post("/internal/shutdown", status_code=202)
    def internal_shutdown(request: Request):
        secret = getattr(request.app.state, "shutdown_secret", None)
        if not secret:
            raise HTTPException(status_code=404)
        token = request.headers.get(SHUTDOWN_TOKEN_HEADER, "")
        if not hmac.compare_digest(token.encode(), secret.encode()):
            raise HTTPException(status_code=403)
        threading.Thread(
            target=_graceful_shutdown, args=(request.app,), daemon=True
        ).start()
        return {"status": "shutting down"}

    @app.get("/internal/models/moonshine")
    def moonshine_status():
        return JSONResponse(moonshine_fetch.download_status())

    @app.post("/internal/models/moonshine", status_code=202)
    async def moonshine_download(request: Request):
        if request.headers.get(INTERNAL_REQUEST_HEADER) != "1":
            raise HTTPException(
                status_code=403,
                detail=f"missing {INTERNAL_REQUEST_HEADER}: 1 header",
            )
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="malformed JSON body")
        accepted = isinstance(payload, dict) and payload.get("accept_license") is True
        if not accepted:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Moonshine weights are under the Moonshine AI Community "
                    f"License ({moonshine_fetch.LICENSE_URL}). POST "
                    '{"accept_license": true} to consent and start the download.'
                ),
            )
        moonshine_fetch.record_license_acceptance()
        started = moonshine_fetch.start_background_download()
        return JSONResponse(
            {**moonshine_fetch.download_status(), "started": started},
            status_code=202,
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "jobs": store.list(),
                "system_message": store.system_message(),
                "include_status": False,
            },
        )

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs(request: Request):
        return templates.TemplateResponse(
            request,
            "_jobs.html",
            {
                "request": request,
                "jobs": store.list(),
                "system_message": store.system_message(),
                "include_status": True,
            },
        )

    # Deprecated: kept for one release for compatibility (D2). The UI now
    # links to the full detail page below instead.
    @app.get("/jobs/{filename}/transcript", response_class=HTMLResponse)
    def transcript(request: Request, filename: str):
        _reject_traversal(filename)
        job = store.get(filename)
        text = ""
        if job and job.output_path and Path(job.output_path).exists():
            text = Path(job.output_path).read_text(encoding="utf-8")
        return templates.TemplateResponse(
            request,
            "_transcript.html",
            {"request": request, "job": job, "text": text},
        )

    @app.get("/jobs/{filename}", response_class=HTMLResponse)
    def job_detail(request: Request, filename: str):
        _reject_traversal(filename)
        job = store.get(filename)
        if job is None:
            raise HTTPException(status_code=404)
        text = ""
        if job.output_path and Path(job.output_path).exists():
            text = Path(job.output_path).read_text(encoding="utf-8")
        stem = Path(filename).stem
        keywords = load_keywords(config.OUTPUT_DIR, stem)
        graph = load_graph(config.OUTPUT_DIR, stem)
        structure = load_structure(config.OUTPUT_DIR, stem)  # file read only
        return templates.TemplateResponse(
            request,
            "detail.html",
            {
                "request": request,
                "job": job,
                "text": text,
                "keywords": keywords["keywords"] if keywords else None,
                "graph": graph["graph"] if graph else None,
                "graph_edits": graph["edits"] if graph else None,
                "graph_edits_url": f"/jobs/{filename}/graph-edits",
                "structure": structure,
                "structure_edits": structure["edits"] if structure else None,
                "structure_edits_url": f"/jobs/{filename}/structure-edits",
            },
        )

    @app.put("/jobs/{filename}/graph-edits")
    async def save_graph_edits(filename: str, request: Request):
        _reject_traversal(filename)
        if graph_edits_body_too_large(request.headers.get("content-length")):
            raise HTTPException(status_code=413, detail="edit request is too large")
        job = store.get(filename)
        if job is None:
            raise HTTPException(status_code=404)
        if job.state != JobState.DONE:
            raise HTTPException(status_code=409, detail="job is not complete")
        raw_body = await _read_limited_graph_edits_body(request)
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="invalid JSON") from None
        if not isinstance(body, dict) or set(body) != {"revision", "edits"}:
            raise HTTPException(status_code=422, detail="invalid edit request")
        try:
            edits = update_graph_edits(
                config.OUTPUT_DIR, Path(filename).stem, body["revision"], body["edits"]
            )
        except GraphRevisionConflict:
            raise HTTPException(status_code=409, detail="edit revision conflict") from None
        except GraphEditsError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        return JSONResponse({"revision": edits["revision"], "edits": edits})

    @app.put("/jobs/{filename}/structure-edits")
    async def save_structure_edits(filename: str, request: Request):
        _reject_traversal(filename)
        if graph_edits_body_too_large(request.headers.get("content-length")):
            raise HTTPException(status_code=413, detail="edit request is too large")
        job = store.get(filename)
        if job is None:
            raise HTTPException(status_code=404)
        if job.state != JobState.DONE:
            raise HTTPException(status_code=409, detail="job is not complete")
        raw_body = await _read_limited_graph_edits_body(request)
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="invalid JSON") from None
        if not isinstance(body, dict) or set(body) != {"revision", "edits"}:
            raise HTTPException(status_code=422, detail="invalid edit request")
        try:
            edits = update_structure_edits(config.OUTPUT_DIR, Path(filename).stem, body["revision"], body["edits"])
        except StructureRevisionConflict:
            raise HTTPException(status_code=409, detail="edit revision conflict") from None
        except StructureEditsError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        return JSONResponse({"revision": edits["revision"], "edits": edits})

    @app.get("/events")
    async def events(request: Request):
        return StreamingResponse(
            event_stream(request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/live", response_class=HTMLResponse)
    def live_page(request: Request):
        return templates.TemplateResponse(
            request,
            "live.html",
            {
                "request": request,
                "desktop_mode": request.app.state.desktop_mode,
            },
        )

    @app.get("/live/status")
    def live_status():
        return JSONResponse(live_manager.status())

    @app.websocket("/live/ws")
    async def live_ws(websocket: WebSocket):
        if not _host_allowed(websocket.headers.get("host", "")):
            await websocket.close(code=1008)  # DNS-rebinding defense
            return
        # No Origin header = feeder role (the Rust native-capture client).
        # Feeders are PCM-only: text frames are ignored and they are not
        # counted as clients (the 60 s auto-finalize tracks browsers only).
        is_feeder = not websocket.headers.get("origin")
        if is_feeder:
            secret = getattr(websocket.app.state, "shutdown_secret", None)
            if secret is not None:
                token = websocket.headers.get(FEEDER_TOKEN_HEADER, "")
                if not hmac.compare_digest(token.encode(), secret.encode()):
                    await websocket.close(code=1008)  # bad/missing feeder token
                    return
        elif not _origin_allowed(websocket):
            await websocket.close(code=1008)  # policy violation
            return
        await websocket.accept()
        loop = asyncio.get_running_loop()
        outbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=LIVE_WS_OUTBOUND_MAXSIZE
        )

        def enqueue(message: dict[str, Any]) -> None:
            try:
                outbound.put_nowait(message)
            except asyncio.QueueFull:
                # Slow client: drop the message. Partials self-heal on the
                # next tick and finals are recoverable via reconnect replay.
                pass

        def listener(message: dict[str, Any]) -> None:
            # Called from worker/session threads — hop onto the event loop.
            loop.call_soon_threadsafe(enqueue, message)

        async def sender() -> None:
            while True:
                message = await outbound.get()
                await websocket.send_json(message)

        live_manager.add_listener(listener)
        if not is_feeder:
            live_manager.client_connected()
        live_manager.replay(listener)
        sender_task = asyncio.create_task(sender())
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes"):
                    try:
                        await asyncio.to_thread(
                            live_manager.feed_pcm, message["bytes"]
                        )
                    except Exception as error:
                        listener(
                            {"type": "error", "message": f"audio error: {error}"}
                        )
                elif message.get("text") and not is_feeder:
                    await _handle_live_control(message["text"], listener)
        except WebSocketDisconnect:
            pass
        finally:
            sender_task.cancel()
            live_manager.remove_listener(listener)
            if not is_feeder:
                live_manager.client_disconnected()

    return app


async def _handle_live_control(
    raw: str, reply: Callable[[dict[str, Any]], None]
) -> None:
    try:
        control = json.loads(raw)
        action = control.get("type")
    except (json.JSONDecodeError, AttributeError):
        reply({"type": "error", "message": "invalid control message"})
        return

    try:
        if action == "start":
            source = control.get("source", "mic")
            await asyncio.to_thread(live_manager.start, source)
        elif action == "stop":
            await asyncio.to_thread(live_manager.stop)
        else:
            reply({"type": "error", "message": f"unknown control type: {action}"})
    except LiveSessionError as error:
        reply({"type": "error", "message": str(error)})
    except Exception as error:
        reply({"type": "error", "message": f"live session error: {error}"})
