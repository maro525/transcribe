# Current Project: NSKETCH-732 — SSE realtime dashboard planning

## Goal
- Replace the dashboard's primary 2-second `/jobs` polling path with Server-Sent Events while keeping a safe fallback.

## Key files
- `src/web/app.py` — FastAPI routes; add planned `/events` endpoint here.
- `src/web/templates/index.html` — current htmx polling entry point; planned native `EventSource` frontend lives here unless static assets are introduced.
- `src/web/templates/_jobs.html` — existing jobs partial retained for fallback/debug.
- `src/status.py` — in-memory `StatusStore`; planned source of truth plus publish/subscribe notifications.
- `src/worker.py` — synchronous worker thread that updates store on discover, processing, segment, done, and error.

## Architecture
- Use native browser `EventSource` with FastAPI `StreamingResponse`.
- Preserve synchronous worker thread and in-memory state.
- Add per-client, thread-safe subscriber queues around `StatusStore` mutations.
- Send an initial `snapshot` on every SSE connection/reconnection, then incremental job/system events and heartbeat.
- Keep `/jobs` and transcript partial routes for fallback and compatibility.

## Decisions
- No new dependency for SSE unless native formatting proves insufficient.
- Prefer snapshot-based reconnect recovery over durable `Last-Event-ID` replay.
- Use `/jobs` polling only as fallback when SSE is unsupported or repeatedly fails.
- Keep htmx for transcript partial loading; do not adopt htmx SSE extension for v1.
