import asyncio
import json
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from pathlib import Path
from queue import Empty
from typing import Any, AsyncIterator, Callable

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from ..status import StoreEvent, store

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

LifespanFactory = Callable[[FastAPI], AbstractAsyncContextManager]
HEARTBEAT_SECONDS = 15
SSE_RETRY_MS = 2000


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
            "_jobs.html",
            {
                "request": request,
                "jobs": store.list(),
                "system_message": store.system_message(),
                "include_status": True,
            },
        )

    @app.get("/jobs/{filename}/transcript", response_class=HTMLResponse)
    def transcript(request: Request, filename: str):
        job = store.get(filename)
        text = ""
        if job and job.output_path and Path(job.output_path).exists():
            text = Path(job.output_path).read_text(encoding="utf-8")
        return templates.TemplateResponse(
            "_transcript.html",
            {"request": request, "job": job, "text": text},
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

    return app
