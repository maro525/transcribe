# Current Project: NSKETCH-732 — SSE realtime dashboard planning

## Goal
- Plan a careful Server-Sent Events migration for the FastAPI Transcribe Dashboard, replacing normal 2-second polling with realtime updates.

## Key files
- `src/web/app.py`
- `src/web/templates/index.html`
- `src/web/templates/_jobs.html`
- `src/status.py`
- `src/worker.py`

## Architecture
- Native `EventSource` frontend.
- FastAPI `StreamingResponse` endpoint at planned `GET /events`.
- `StatusStore` remains the in-memory source of truth and gains publish/subscribe notification support.
- Existing `/jobs` htmx partial remains as fallback/debug route.

## Decisions
- Avoid new dependencies for v1.
- Preserve synchronous worker thread model.
- Send fresh `snapshot` on every connection/reconnect instead of implementing event replay.
- Use heartbeat and bounded subscriber queues to make long-lived streams robust.
