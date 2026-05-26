import os
from pathlib import Path

from dotenv import load_dotenv


def load_hf_token(env_file: Path | None = None) -> str:
    if env_file and env_file.exists():
        load_dotenv(env_file)
        print(f"Loaded .env: {env_file}")

    token = os.environ.get("HF_TOKEN")
    if token:
        return token

    raise ValueError(
        "HF_TOKEN が見つかりません。環境変数または .env に設定してください。"
    )
