#!/usr/bin/env python3
"""Download the ggml Whisper model used by the live transcription mode.

The weights are hosted on Hugging Face (ggerganov/whisper.cpp) and are NOT
committed to this repository. Run this once before using /live:

    python scripts/fetch_live_model.py            # default quant (q8_0)
    python scripts/fetch_live_model.py --quant q5_0

huggingface_hub is already installed transitively via pyannote.audio.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402

HF_REPO_ID = "ggerganov/whisper.cpp"
KNOWN_QUANTS = ("f16", "q8_0", "q5_0")


def model_filename(quant: str) -> str:
    if quant == "f16":
        return "ggml-large-v3-turbo.bin"
    return f"ggml-large-v3-turbo-{quant}.bin"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quant",
        default=config.LIVE_MODEL_QUANT,
        help=(
            "Quantization to download "
            f"(known: {', '.join(KNOWN_QUANTS)}; default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=config.BASE_DIR / "models",
        help="Destination directory (default: %(default)s)",
    )
    args = parser.parse_args()

    if args.quant not in KNOWN_QUANTS:
        print(f"warning: unknown quant '{args.quant}', trying anyway", file=sys.stderr)

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print(
            "error: huggingface_hub is not installed. "
            "Run: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    filename = model_filename(args.quant)
    args.dest.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {HF_REPO_ID}/{filename} -> {args.dest}/")
    path = hf_hub_download(
        repo_id=HF_REPO_ID,
        filename=filename,
        local_dir=str(args.dest),
    )
    print(f"Done: {path}")
    print(
        "Set LIVE_MODEL_PATH or LIVE_MODEL_QUANT if you downloaded "
        "a non-default quantization."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
