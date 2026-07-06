"""Unit tests for src/live/terms.py (pluggable term extraction).

JanomeTermExtractor is tested via an injected FakeTokenizer so the tests run
(and the POS filter / compound merge / scoring stay covered) in environments
without janome installed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.live.terms import (  # noqa: E402
    JanomeTermExtractor,
    LegacyTermExtractor,
    Term,
    extract_terms,
    get_term_extractor,
    reset_extractor_cache,
)


class FakeToken:
    def __init__(self, surface: str, part_of_speech: str, base_form: str | None = None):
        self.surface = surface
        self.part_of_speech = part_of_speech
        self.base_form = surface if base_form is None else base_form


class FakeTokenizer:
    def __init__(self, tokens: list[FakeToken]):
        self._tokens = tokens

    def tokenize(self, text: str):
        return iter(self._tokens)


def _extract(tokens: list[FakeToken], limit: int = 10) -> list[Term]:
    return JanomeTermExtractor(tokenizer=FakeTokenizer(tokens)).extract("dummy", limit)


def test_nouns_are_adopted_and_other_pos_dropped():
    terms = _extract(
        [
            FakeToken("会議", "名詞,一般,*,*"),
            FakeToken("を", "助詞,格助詞,*,*"),
            FakeToken("行う", "動詞,自立,*,*"),
        ]
    )
    assert [t.word for t in terms] == ["会議"]


def test_pronoun_nonindependent_and_number_nouns_are_excluded():
    terms = _extract(
        [
            FakeToken("それ", "名詞,代名詞,一般,*"),
            FakeToken("こと", "名詞,非自立,一般,*"),
            FakeToken("三", "名詞,数,*,*"),
            FakeToken("予算", "名詞,一般,*,*"),
        ]
    )
    assert [t.word for t in terms] == ["予算"]


def test_consecutive_nouns_merge_into_compound_with_bonus():
    terms = _extract(
        [
            FakeToken("音声", "名詞,一般,*,*"),
            FakeToken("認識", "名詞,サ変接続,*,*"),
            FakeToken("の", "助詞,連体化,*,*"),
            FakeToken("精度", "名詞,一般,*,*"),
        ]
    )
    words = {t.word: t.score for t in terms}
    assert set(words) == {"音声認識", "精度"}
    # compound: 1 * (1 + 4*0.08) * 1.25 = 1.65 ; simple: 1 * (1 + 2*0.08) = 1.16
    assert abs(words["音声認識"] - 1.65) < 1e-9
    assert abs(words["精度"] - 1.16) < 1e-9
    assert terms[0].word == "音声認識"  # sorted by score desc


def test_excluded_noun_breaks_a_compound_run():
    # noun / excluded-noun / noun must NOT merge across the exclusion
    terms = _extract(
        [
            FakeToken("予算", "名詞,一般,*,*"),
            FakeToken("三", "名詞,数,*,*"),
            FakeToken("会議", "名詞,一般,*,*"),
        ]
    )
    assert {t.word for t in terms} == {"予算", "会議"}


def test_base_form_normalizes_and_star_falls_back_to_surface():
    terms = _extract(
        [
            FakeToken("サーバー", "名詞,一般,*,*", base_form="サーバ"),
            FakeToken("は", "助詞,係助詞,*,*"),
            FakeToken("GPUs", "名詞,固有名詞,*,*", base_form="*"),
        ]
    )
    assert {t.word for t in terms} == {"サーバ", "gpus"}  # ASCII lowered


def test_single_char_words_are_dropped():
    terms = _extract(
        [
            FakeToken("A", "名詞,固有名詞,*,*", base_form="*"),
            FakeToken("は", "助詞,係助詞,*,*"),
            FakeToken("木", "名詞,一般,*,*"),
        ]
    )
    assert terms == []


def test_repeated_word_accumulates_count():
    terms = _extract(
        [
            FakeToken("予算", "名詞,一般,*,*"),
            FakeToken("と", "助詞,並立助詞,*,*"),
            FakeToken("予算", "名詞,一般,*,*"),
        ]
    )
    assert len(terms) == 1
    assert terms[0].word == "予算"
    assert abs(terms[0].score - 2 * (1 + 2 * 0.08)) < 1e-9


def test_limit_truncates_and_zero_limit_is_safe():
    tokens = [
        FakeToken("予算", "名詞,一般,*,*"),
        FakeToken("と", "助詞,並立助詞,*,*"),
        FakeToken("会議", "名詞,一般,*,*"),
        FakeToken("と", "助詞,並立助詞,*,*"),
        FakeToken("議事録", "名詞,一般,*,*"),
    ]
    assert len(_extract(tokens, limit=2)) == 2
    assert _extract(tokens, limit=0) == []


def test_get_term_extractor_selects_by_janome_availability():
    reset_extractor_cache()
    try:
        extractor = get_term_extractor()
        try:
            import janome.tokenizer  # noqa: F401

            assert isinstance(extractor, JanomeTermExtractor)
        except ImportError:
            assert isinstance(extractor, LegacyTermExtractor)
        # cache: second call returns the same instance
        assert get_term_extractor() is extractor
    finally:
        reset_extractor_cache()


def test_extract_terms_returns_term_list():
    reset_extractor_cache()
    try:
        result = extract_terms("会議の予算とスケジュールについて話します", limit=5)
        assert isinstance(result, list)
        assert len(result) <= 5
        for term in result:
            assert isinstance(term, Term)
            assert isinstance(term.word, str) and term.word
            assert isinstance(term.score, float)
    finally:
        reset_extractor_cache()


def test_legacy_extractor_maps_keywords_to_terms():
    result = LegacyTermExtractor().extract("会議の予算とスケジュールについて", limit=3)
    assert isinstance(result, list)
    assert len(result) <= 3
    for term in result:
        assert isinstance(term, Term)


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
