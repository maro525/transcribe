import asyncio
import json
import os
import socket
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from src import config, ffmpeg_patch
from src.live.recovery import recover_orphaned_wavs
from src.version import BACKEND_VERSION, PROTOCOL_VERSION
from src.web.app import create_app

# Set to "1" by the desktop (Tauri) shell: bind (WEB_HOST, 0) ourselves and
# announce the chosen port on stdout instead of using the fixed WEB_PORT.
DYNAMIC_PORT_ENV_VAR = "TRANSCRIBE_DYNAMIC_PORT"

# timeout_graceful_shutdown: force-close lingering SSE/WebSocket connections
# a few seconds after shutdown starts instead of waiting for them
# indefinitely, so the server actually stops even with a browser tab open.
GRACEFUL_SHUTDOWN_SECONDS = 5


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Lazy import: the batch worker pulls in the whole ML stack (torch,
    # whisper, pyannote); keeping it out of module scope leaves main.py
    # importable in lightweight environments (unit tests, tooling).
    from src import worker

    threading.Thread(target=worker.run, daemon=True).start()
    yield


def _dynamic_port_enabled() -> bool:
    return os.environ.get(DYNAMIC_PORT_ENV_VAR) == "1"


def _tauri_ready_line(port: int) -> str:
    """The single-line stdout handshake the Tauri shell waits for."""
    payload = json.dumps(
        {
            "port": port,
            "protocol": PROTOCOL_VERSION,
            "backend_version": BACKEND_VERSION,
        }
    )
    return f"TAURI_READY {payload}"


def _serve_dynamic_port(app: FastAPI) -> None:
    """Desktop mode: bind port 0, print TAURI_READY, serve on that socket.

    The uvicorn server instance is stored on ``app.state`` so
    POST /internal/shutdown (src/web/app.py) can flip ``should_exit`` for a
    graceful stop; the shutdown secret itself is read from the environment
    in ``create_app``.
    """
    sock = socket.create_server((config.WEB_HOST, 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        )
    )
    app.state.uvicorn_server = server
    print(_tauri_ready_line(port), flush=True)
    asyncio.run(server.serve(sockets=[sock]))


def main() -> None:
    # Must run before any ffmpeg use (batch conversion and whisper's
    # internal calls): FFMPEG_PATH resolution + no console flash on Windows.
    ffmpeg_patch.apply()
    config.ensure_directories()
    # Re-ingest live recordings orphaned by a previous hard shutdown.
    recover_orphaned_wavs(config.TEMP_DIR, config.SOURCE_DIR)

    from src import worker  # lazy: see lifespan

    worker.bootstrap_history()

    app = create_app(lifespan=lifespan)
    if _dynamic_port_enabled():
        _serve_dynamic_port(app)
        return
    uvicorn.run(
        app,
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )


if __name__ == "__main__":
    main()
