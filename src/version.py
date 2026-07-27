"""Backend version / protocol constants for the desktop (Tauri) integration.

Single source of truth consumed by both the TAURI_READY stdout handshake
(main.py) and GET /healthz (src/web/app.py). The Rust shell compares
PROTOCOL_VERSION to decide whether the bundled backend is compatible.
Stdlib-only on purpose: importable from anywhere without side effects.
"""
from __future__ import annotations

BACKEND_VERSION = "2026.07.0"
PROTOCOL_VERSION = 1
