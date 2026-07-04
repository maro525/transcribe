"""Unit tests for src.live.keywords."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.live.keywords import Keyword, extract_keywords  # noqa: E402


def test_extract_keywords_with_repeated_terms_ranks_by_frequency():
    text = "リリースの話。リリース日程とデプロイ。リリース準備。デプロイ手順。"
    keywords = extract_keywords(text, limit=5)
    words = [k.word for k in keywords]
    assert words[0] == "リリース"
    assert "デプロイ" in words


def test_extract_keywords_with_empty_text_returns_empty():
    assert extract_keywords("", limit=5) == []
    assert extract_keywords("こんにちは", limit=0) == []


def test_extract_keywords_excludes_stopwords():
    text = "会議で資料を確認。ミーティングの内容を共有。"
    words = [k.word for k in extract_keywords(text, limit=10)]
    assert "会議" not in words
    assert "ミーティング" not in words


def test_extract_keywords_finds_kanji_katakana_and_alnum():
    text = "議事録をNotionに保存。議事録テンプレートとカレンダー連携。API仕様も。"
    words = [k.word for k in extract_keywords(text, limit=10)]
    assert "議事録" in words
    assert "Notion" in words
    assert "カレンダー" in words
    assert "API" in words


def test_extract_keywords_respects_limit_and_is_deterministic():
    text = "アルファ ベータ ガンマ デルタ " * 3
    first = extract_keywords(text, limit=2)
    second = extract_keywords(text, limit=2)
    assert len(first) == 2
    assert first == second
    assert all(isinstance(k, Keyword) for k in first)


def test_extract_keywords_length_bonus_breaks_frequency_ties():
    text = "システムアーキテクチャ 設計 システムアーキテクチャ 設計"
    keywords = extract_keywords(text, limit=2)
    assert keywords[0].word == "システムアーキテクチャ"
    assert keywords[0].score > keywords[1].score


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
