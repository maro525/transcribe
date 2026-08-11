import os
from pathlib import Path

from dotenv import load_dotenv


def load_hf_token(env_file: Path | None = None) -> str | None:
    """Return the Hugging Face token, or None when it is not configured.

    The token is optional (the desktop setup screen marks it 任意): without
    it the batch worker still transcribes, just without speaker diarization
    (single-speaker output), instead of failing to start.
    """
    if env_file and env_file.exists():
        load_dotenv(env_file)
        print(f"Loaded .env: {env_file}")

    token = os.environ.get("HF_TOKEN")
    return token or None
