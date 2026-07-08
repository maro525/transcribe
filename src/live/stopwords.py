"""Non-content word lists for the live/batch word-network term filters.

Pure stdlib constants (no imports from the package, so ``terms.py`` can import
this module without circularity). Two frozen sets are exposed:

- ``STOPWORDS``: generic/function-like nouns that carry no topical content
  (こと・もの・ため …). They frequently survive POS filtering — especially on
  the janome-absent Legacy path — and clutter the network.
- ``FILLERS``: conversational fillers and backchannel tokens typical of
  meeting transcripts (えーと・なんか・やっぱり …).

``TERMS_EXTRA_STOPWORDS`` (comma-separated env var) appends deployment-local
words without a code change. Matching is done on the extractor's normalized
form (base form, ASCII lowered) via :func:`is_content_word`.
"""
from __future__ import annotations

import os

STOPWORDS = frozenset(
    {
        # generic nouns (形式名詞・汎用語)
        "こと",
        "もの",
        "ため",
        "よう",
        "とき",
        "ところ",
        "ほう",
        "はず",
        "つもり",
        "わけ",
        "まま",
        "かたち",
        "感じ",
        "場合",
        "あたり",
        "うち",
        "たち",
        "それぞれ",
        "自分",
        "皆さん",
        "みなさん",
        # temporal nouns that slip through as 名詞,一般 on some dictionaries
        "今日",
        "明日",
        "昨日",
        "今回",
        "次回",
        "前回",
        "最近",
        "現在",
        "今後",
        "以上",
        "以下",
        "以降",
        "以前",
    }
)

FILLERS = frozenset(
    {
        "えー",
        "えーと",
        "えっと",
        "ええと",
        "あー",
        "あのー",
        "あのう",
        "そのー",
        "うーん",
        "うん",
        "はい",
        "ええ",
        "まあ",
        "まぁ",
        "なんか",
        "ちょっと",
        "やっぱり",
        "やっぱ",
        "やはり",
        "そうそう",
        "なるほど",
        "とりあえず",
        "ほんと",
        "ほんとう",
        "本当",
        "たぶん",
        "多分",
    }
)


def _extra_stopwords() -> frozenset[str]:
    """Deployment-local additions from TERMS_EXTRA_STOPWORDS (read per call
    so tests and long-running processes see env changes)."""
    raw = os.environ.get("TERMS_EXTRA_STOPWORDS", "")
    return frozenset(word.strip().lower() for word in raw.split(",") if word.strip())


def is_content_word(word: str) -> bool:
    """True when ``word`` (normalized form) should stay in the network."""
    lowered = word.lower()
    if lowered in STOPWORDS or lowered in FILLERS:
        return False
    return lowered not in _extra_stopwords()
