import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from src import config, worker
from src.web.app import create_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=worker.run, daemon=True).start()
    yield


def main() -> None:
    config.ensure_directories()
    worker.bootstrap_history()

    app = create_app(lifespan=lifespan)
    # timeout_graceful_shutdown: force-close lingering SSE/WebSocket connections
    # a few seconds after Ctrl+C instead of waiting for them indefinitely, so the
    # server actually stops even with a browser tab still open.
    uvicorn.run(
        app,
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()
