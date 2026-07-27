"""Moonshine weights download behind an explicit license-consent gate.

The UsefulSensors/moonshine-tiny-ja weights are distributed under the
Moonshine AI Community License (NOT MIT — non-commercial / registration
terms: https://moonshine.ai/community-license). They are therefore never
bundled and never auto-downloaded. The only two entry points are:

- ``POST /internal/models/moonshine`` (src/web/app.py) with an explicit
  ``{"accept_license": true}`` body, which records the consent to
  ``BASE_DIR/moonshine_license.json`` and starts a background download;
- ``scripts/fetch_moonshine_model.py``, a thin CLI wrapper around
  ``download_moonshine_model`` for dev use.

huggingface_hub is imported lazily inside the download so this module stays
importable everywhere (it is stdlib + src.config only at import time).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import config

HF_REPO_ID = "UsefulSensors/moonshine-tiny-ja"
LICENSE_URL = "https://moonshine.ai/community-license"
LICENSE_FILE_NAME = "moonshine_license.json"
ALLOW_PATTERNS = ["*.json", "*.safetensors", "LICENSE*", "README*"]

DOWNLOAD_IDLE = "idle"
DOWNLOAD_RUNNING = "downloading"
DOWNLOAD_DONE = "done"
DOWNLOAD_FAILED = "failed"

LICENSE_NOTE = f"""\

[LICENSE] Moonshine AI Community License (not MIT).
  - Research / non-commercial use: free.
  - Commercial use (including internal business use): free under $1M annual
    revenue, but registration is REQUIRED:
        {LICENSE_URL}
"""

_lock = threading.Lock()
_status = DOWNLOAD_IDLE
_error: str | None = None


# --- license consent ---------------------------------------------------------

def license_file_path() -> Path:
    return config.BASE_DIR / LICENSE_FILE_NAME


def license_accepted(path: Path | None = None) -> bool:
    """True when a prior consent record exists and is well-formed."""
    record_path = path or license_file_path()
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(record.get("accepted_at"))


def record_license_acceptance(path: Path | None = None) -> dict[str, Any]:
    """Persist the consent so re-downloads don't re-ask. Returns the record."""
    record_path = path or license_file_path()
    record = {
        "accepted_at": datetime.now().isoformat(timespec="seconds"),
        "license_url": LICENSE_URL,
        "repo_id": HF_REPO_ID,
    }
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return record


# --- download ----------------------------------------------------------------

def weights_present(dest: Path | None = None) -> bool:
    return ((dest or config.LIVE_MOONSHINE_MODEL_DIR) / "model.safetensors").exists()


def download_moonshine_model(dest: Path | None = None) -> Path:
    """Blocking download of the moonshine weights into ``dest``.

    Caller is responsible for the license gate — this function only fetches.
    """
    from huggingface_hub import snapshot_download

    target = Path(dest or config.LIVE_MOONSHINE_MODEL_DIR)
    target.mkdir(parents=True, exist_ok=True)
    return Path(
        snapshot_download(
            repo_id=HF_REPO_ID,
            local_dir=str(target),
            allow_patterns=ALLOW_PATTERNS,
        )
    )


def start_background_download(dest: Path | None = None) -> bool:
    """Kick off the download on a daemon thread. False when already running."""
    global _status, _error
    with _lock:
        if _status == DOWNLOAD_RUNNING:
            return False
        _status = DOWNLOAD_RUNNING
        _error = None

    def _run() -> None:
        global _status, _error
        try:
            download_moonshine_model(dest)
        except Exception as error:
            with _lock:
                _status = DOWNLOAD_FAILED
                _error = str(error)
        else:
            with _lock:
                _status = DOWNLOAD_DONE

    threading.Thread(target=_run, daemon=True, name="moonshine-download").start()
    return True


def download_status() -> dict[str, Any]:
    with _lock:
        status, error = _status, _error
    return {
        "status": status,
        "error": error,
        "dest": str(config.LIVE_MOONSHINE_MODEL_DIR),
        "license_accepted": license_accepted(),
        "weights_present": weights_present(),
    }
