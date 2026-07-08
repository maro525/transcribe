import asyncio
import json
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
from fastapi.templating import Jinja2Templates

from .. import config
from ..artifacts import load_graph, load_keywords, load_structure
from ..live.session import LiveSessionError, manager as live_manager
from ..status import StoreEvent, store

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

LifespanFactory = Callable[[FastAPI], AbstractAsyncContextManager]
HEARTBEAT_SECONDS = 15
SSE_RETRY_MS = 2000
LIVE_WS_OUTBOUND_MAXSIZE = 1000


def _origin_allowed(websocket: WebSocket) -> bool:
    """Reject cross-site WebSocket connections (CSWSH defense).

    Browsers always send Origin on WS handshakes; non-browser clients
    (no Origin header) are allowed, matching the dashboard's trust model.
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


def create_app(lifespan: LifespanFactory | None = None) -> FastAPI:
    app = FastAPI(title="Transcribe Dashboard", lifespan=lifespan)

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
                "structure": structure,
            },
        )

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
        return templates.TemplateResponse(request, "live.html", {"request": request})

    @app.get("/live/status")
    def live_status():
        return JSONResponse(live_manager.status())

    @app.websocket("/live/ws")
    async def live_ws(websocket: WebSocket):
        if not _origin_allowed(websocket):
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
                elif message.get("text"):
                    await _handle_live_control(message["text"], listener)
        except WebSocketDisconnect:
            pass
        finally:
            sender_task.cancel()
            live_manager.remove_listener(listener)
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
