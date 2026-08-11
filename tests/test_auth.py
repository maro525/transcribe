"""HF token resolution is optional: no token -> None (diarization disabled),
never an exception. The desktop setup screen marks the token 任意, so a
missing token must not kill the batch worker (alpha.2 regression).
"""
import os

from src.auth import load_hf_token


def test_returns_none_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert load_hf_token(tmp_path / "missing.env") is None


def test_returns_none_for_empty_token(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "")
    assert load_hf_token(None) is None


def test_returns_token_from_env(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test123")
    assert load_hf_token(None) == "hf_test123"


def test_loads_token_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("HF_TOKEN=hf_from_file\n", encoding="utf-8")
    try:
        assert load_hf_token(env_file) == "hf_from_file"
    finally:
        # load_dotenv mutates os.environ outside monkeypatch's tracking.
        os.environ.pop("HF_TOKEN", None)
