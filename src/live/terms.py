"""Pluggable term extraction for the live "word network" panel.

The primary extractor uses janome (pure-Python Japanese morphological
analyzer): it adopts noun/proper-noun morphemes (excluding pronouns,
non-independent nouns and numerals), merges consecutive nouns into compound
words, and normalizes each morpheme to its base form. When janome is not
installed, the module transparently falls back to the existing lightweight
extractor in ``keywords.py`` (imported, never modified), so live mode keeps
working with no extra dependency.

Future work: session-cumulative TF-IDF style weighting is out of scope for
now (scores are frequency * length heuristics, see JanomeTermExtractor).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .stopwords import is_content_word

MAX_LENGTH_BONUS_CHARS = 12
LENGTH_BONUS_PER_CHAR = 0.08
COMPOUND_BONUS = 1.25

_NOUN_MAJOR = "名詞"
# 副詞可能 (adverbial nouns: 今日・少し…) and 接尾 (suffix nouns: ～的・～性
# as lone morphemes) carry no topical content on their own.
_EXCLUDED_NOUN_SUBTYPES = {"代名詞", "非自立", "数", "副詞可能", "接尾"}


@dataclass(frozen=True)
class Term:
    """One extracted term: normalized word + extraction score (salience)."""

    word: str
    score: float


class TermExtractor(Protocol):
    def extract(self, text: str, limit: int) -> list[Term]: ...


class LegacyTermExtractor:
    """janome-absent fallback: delegate to the existing keyword extractor."""

    def extract(self, text: str, limit: int) -> list[Term]:
        from .keywords import extract_keywords  # imported lazily, never modified

        terms = [Term(k.word, float(k.score)) for k in extract_keywords(text, limit)]
        # Post-filter (keywords.py itself is never modified): drop stopwords
        # and fillers so both extraction paths share the content-word policy.
        return [t for t in terms if is_content_word(t.word)]


class JanomeTermExtractor:
    """Noun/compound-noun extraction via janome morphological analysis.

    A tokenizer can be injected for tests (it must yield tokens exposing
    ``surface``, ``base_form`` and the comma-joined ``part_of_speech``).
    """

    def __init__(self, tokenizer: Any = None) -> None:
        if tokenizer is None:
            from janome.tokenizer import Tokenizer

            tokenizer = Tokenizer()
        self._tokenizer = tokenizer

    def extract(self, text: str, limit: int) -> list[Term]:
        counts: dict[str, int] = {}
        morpheme_counts: dict[str, int] = {}  # word -> morphemes merged into it
        run: list[str] = []  # consecutive adopted nouns (compound in progress)

        def flush() -> None:
            if not run:
                return
            word = "".join(run)
            if word.isascii():
                word = word.lower()
            # drop one-char words / lone symbols / stopwords / fillers
            if len(word) > 1 and is_content_word(word):
                counts[word] = counts.get(word, 0) + 1
                morpheme_counts[word] = len(run)
            run.clear()

        for token in self._tokenizer.tokenize(text):
            pos = token.part_of_speech.split(",")
            major = pos[0]
            sub = pos[1] if len(pos) > 1 else ""
            if major == _NOUN_MAJOR and sub not in _EXCLUDED_NOUN_SUBTYPES:
                base = token.base_form
                run.append(base if base and base != "*" else token.surface)
            else:
                flush()
        flush()

        terms: list[Term] = []
        for word, count in counts.items():
            score = count * (
                1 + min(len(word), MAX_LENGTH_BONUS_CHARS) * LENGTH_BONUS_PER_CHAR
            )
            if morpheme_counts[word] >= 2:
                score *= COMPOUND_BONUS
            terms.append(Term(word, score))
        terms.sort(key=lambda t: (-t.score, t.word))
        return terms[: max(0, limit)]


_extractor: TermExtractor | None = None


def get_term_extractor() -> TermExtractor:
    """Module-level cached extractor; janome import is attempted once."""
    global _extractor
    if _extractor is None:
        try:
            import janome.tokenizer  # noqa: F401

            _extractor = JanomeTermExtractor()
        except Exception:
            _extractor = LegacyTermExtractor()
    return _extractor


def extract_terms(text: str, limit: int) -> list[Term]:
    """The single entry point used by the live session."""
    return get_term_extractor().extract(text, limit)


def reset_extractor_cache() -> None:
    """Test hook: force extractor re-selection."""
    global _extractor
    _extractor = None
