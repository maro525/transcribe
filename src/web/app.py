from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..status import store

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

LifespanFactory = Callable[[FastAPI], AbstractAsyncContextManager]


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

    return app
