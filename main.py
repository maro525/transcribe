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
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT)


if __name__ == "__main__":
    main()
