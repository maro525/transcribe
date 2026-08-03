#!/usr/bin/env python3
"""Download UsefulSensors/moonshine-tiny-ja weights for the CPU live engine.

Thin CLI wrapper around ``src.live.moonshine_fetch.download_moonshine_model``
(the desktop app uses the same logic via POST /internal/models/moonshine).
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
from src.live import moonshine_fetch  # noqa: E402

HF_REPO_ID = moonshine_fetch.HF_REPO_ID  # backward-compat re-export


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=Path,
        default=config.LIVE_MOONSHINE_MODEL_DIR,
        help="Destination directory (default: %(default)s)",
    )
    args = parser.parse_args()

    print(moonshine_fetch.LICENSE_NOTE)
    print(f"Downloading {HF_REPO_ID} -> {args.dest}/")
    try:
        path = moonshine_fetch.download_moonshine_model(args.dest)
    except ImportError:
        print(
            "error: huggingface_hub is not installed. "
            "Run: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    print(f"Done: {path}")
    print(
        "Set LIVE_ENGINE=moonshine to use these weights (or LIVE_ENGINE=auto "
        "on a non-CUDA host)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
