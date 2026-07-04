"""Keyword extraction for the live transcription view.

Dependency-free: regex candidate extraction (katakana / kanji / latin-alnum
runs) + frequency x length scoring + a stopword list. Designed to be swapped
for a morphological analyzer or an LLM later — callers only rely on
``extract_keywords(text, limit) -> list[Keyword]``.

Only *finalized* utterance text should be fed in (partials are volatile and
would make the keyword list flicker).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

# Candidate patterns: katakana runs (3+), kanji runs (2+), latin/alnum words (2+).
_KATAKANA = re.compile(r"[ァ-ヺー]{3,}")
_KANJI = re.compile(r"[一-鿿々]{2,}")
_ALNUM = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]+")

# Generic words that are frequent in meetings but carry no topical signal.
_STOPWORDS = frozenset(
    {
        # kanji generics
        "今日", "明日", "昨日", "今回", "次回", "前回", "今週", "来週", "先週",
        "今月", "来月", "先月", "今年", "来年", "去年", "午前", "午後",
        "会議", "打合", "打合せ", "資料", "確認", "共有", "連絡", "相談",
        "報告", "対応", "検討", "調整", "依頼", "作業", "説明", "質問",
        "回答", "課題", "状況", "内容", "結果", "予定", "時間", "場合",
        "感じ", "自分", "皆さん", "全体", "一旦", "最初", "最後", "以上",
        "本日", "現在", "現状", "一番", "部分", "話者", "発言",
        # katakana generics
        "ミーティング", "アジェンダ", "メンバー", "スケジュール", "タイミング",
        "イメージ", "ベース", "レベル", "ポイント", "パターン", "ケース",
        # latin generics
        "ok", "ng", "am", "pm", "etc", "vs",
    }
)

_LENGTH_BONUS_CAP = 10
_LENGTH_BONUS_RATE = 0.1


@dataclass(frozen=True)
class Keyword:
    word: str
    score: float


def extract_keywords(text: str, limit: int = 15) -> list[Keyword]:
    """Extract the top ``limit`` keywords from ``text``.

    Score = occurrence count x (1 + 0.1 * min(len(word), 10)); longer terms
    win ties against short generic ones. Deterministic ordering
    (score desc, then word) so repeated calls with the same text are stable.
    """
    if not text or limit <= 0:
        return []

    counts: Counter[str] = Counter()
    for pattern in (_KATAKANA, _KANJI, _ALNUM):
        for word in pattern.findall(text):
            if word in _STOPWORDS or word.lower() in _STOPWORDS:
                continue
            counts[word] += 1

    keywords = [
        Keyword(
            word=word,
            score=round(
                count
                * (1.0 + _LENGTH_BONUS_RATE * min(len(word), _LENGTH_BONUS_CAP)),
                3,
            ),
        )
        for word, count in counts.items()
    ]
    keywords.sort(key=lambda k: (-k.score, k.word))
    return keywords[:limit]
