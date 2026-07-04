#!/usr/bin/env python3
"""Download UsefulSensors/moonshine-tiny-ja weights for the CPU live engine.

The weights are hosted on Hugging Face and are NOT committed to this
repository (models/ is gitignored). Run this once before using
LIVE_ENGINE=moonshine (or LIVE_ENGINE=auto on a non-GPU host):

    python scripts/fetch_moonshine_model.py

The repo is public (not gated), so no HF_TOKEN is needed. huggingface_hub is
already installed transitively via pyannote.audio.

LICENSE: the model is distributed under the Moonshine AI Community License
(NOT MIT). Research / non-commercial use is free. Commercial use — including
internal business use — is free under $1M annual revenue but REQUIRES
registration at https://moonshine.ai/community-license . Make sure your usage
is registered before deploying.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402

HF_REPO_ID = "UsefulSensors/moonshine-tiny-ja"
_LICENSE_NOTE = """\

[LICENSE] Moonshine AI Community License (not MIT).
  - Research / non-commercial use: free.
  - Commercial use (including internal business use): free under $1M annual
    revenue, but registration is REQUIRED:
        https://moonshine.ai/community-license
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=Path,
        default=config.LIVE_MOONSHINE_MODEL_DIR,
        help="Destination directory (default: %(default)s)",
    )
    args = parser.parse_args()

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print(
            "error: huggingface_hub is not installed. "
            "Run: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    print(_LICENSE_NOTE)
    args.dest.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {HF_REPO_ID} -> {args.dest}/")
    path = snapshot_download(
        repo_id=HF_REPO_ID,
        local_dir=str(args.dest),
        allow_patterns=["*.json", "*.safetensors", "LICENSE*", "README*"],
    )
    print(f"Done: {path}")
    print(
        "LIVE_ENGINE=auto now selects moonshine on non-CUDA hosts with these "
        "weights present; set LIVE_ENGINE=moonshine to force it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
