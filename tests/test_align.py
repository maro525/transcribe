"""Unit tests for pure word->speaker alignment (src.align).

No torch / whisper / pyannote / audio / GPU: imports only src.align. Covers the
NSKETCH-885 Design case matrix. Runnable directly: python3 tests/test_align.py
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.align import (  # noqa: E402
    TranscriptSegment,
    Turn,
    Word,
    assign_words_to_turns,
    words_from_whisper_result,
)


def _w(text, start, end):
    return Word(text=text, start=start, end=end)


def _t(speaker, start, end):
    return Turn(speaker=speaker, start=start, end=end)


# --- grouping / no-space join -----------------------------------------------

def test_same_speaker_words_join_without_spaces():
    words = [_w("こん", 0.0, 0.5), _w("にちは", 0.5, 1.0)]
    turns = [_t("SPEAKER_01", 0.0, 1.0)]
    out = assign_words_to_turns(words, turns)
    assert out == [TranscriptSegment("SPEAKER_01", 0.0, 1.0, "こんにちは")]


def test_speaker_change_splits_into_two_segments():
    words = [_w("はい", 0.0, 1.0), _w("いいえ", 2.0, 3.0)]
    turns = [_t("A", 0.0, 1.5), _t("B", 1.5, 3.0)]
    out = assign_words_to_turns(words, turns)
    assert [(s.speaker, s.text) for s in out] == [("A", "はい"), ("B", "いいえ")]


def test_same_speaker_across_two_turns_stays_one_segment():
    words = [_w("あ", 0.0, 1.0), _w("い", 2.0, 3.0)]
    turns = [_t("A", 0.0, 1.5), _t("A", 1.5, 3.0)]
    out = assign_words_to_turns(words, turns)
    assert len(out) == 1 and out[0].text == "あい"


# --- overlap boundary / max-overlap wins ------------------------------------

def test_max_overlap_wins():
    word = _w("x", 0.0, 10.0)
    turns = [_t("A", 0.0, 3.0), _t("B", 3.0, 10.0)]  # B overlaps 7 > A's 3
    assert assign_words_to_turns([word], turns)[0].speaker == "B"


def test_partial_overlap_beats_zero_overlap():
    word = _w("x", 4.0, 6.0)
    turns = [_t("A", 0.0, 5.0), _t("B", 10.0, 20.0)]  # A overlaps 1, B none
    assert assign_words_to_turns([word], turns)[0].speaker == "A"


# --- equal overlap tie -> earliest turn -------------------------------------

def test_equal_overlap_tie_goes_to_earlier_turn():
    word = _w("x", 0.0, 10.0)
    turns = [_t("A", 0.0, 5.0), _t("B", 5.0, 10.0)]  # each overlap 5
    assert assign_words_to_turns([word], turns)[0].speaker == "A"


# --- zero overlap -> nearest (before / after / between) ---------------------

def test_zero_overlap_nearest_after():
    word = _w("x", 0.0, 1.0)
    turns = [_t("A", 5.0, 6.0), _t("B", 2.0, 3.0)]  # B nearer (gap 1 vs 4)
    assert assign_words_to_turns([word], turns)[0].speaker == "B"


def test_zero_overlap_nearest_before():
    word = _w("x", 10.0, 11.0)
    turns = [_t("A", 0.0, 1.0), _t("B", 8.0, 9.0)]  # B nearer (gap 1 vs 9)
    assert assign_words_to_turns([word], turns)[0].speaker == "B"


def test_zero_overlap_between_two_turns_picks_closer():
    word = _w("x", 4.5, 5.0)
    turns = [_t("A", 0.0, 4.0), _t("B", 6.0, 8.0)]  # gap A=0.5, B=1.0
    assert assign_words_to_turns([word], turns)[0].speaker == "A"


def test_zero_overlap_equidistant_tie_goes_to_earlier_turn():
    word = _w("x", 4.0, 5.0)
    turns = [_t("A", 0.0, 3.0), _t("B", 6.0, 9.0)]  # gap A=1, B=1
    assert assign_words_to_turns([word], turns)[0].speaker == "A"


# --- zero-duration (point) words --------------------------------------------

def test_point_word_inside_turn_assigns_to_that_turn():
    word = _w("x", 5.0, 5.0)
    turns = [_t("A", 0.0, 2.0), _t("B", 4.0, 6.0)]  # point inside B, gap 0
    assert assign_words_to_turns([word], turns)[0].speaker == "B"


def test_point_word_on_shared_boundary_ties_to_earlier():
    word = _w("x", 5.0, 5.0)
    turns = [_t("A", 0.0, 5.0), _t("B", 5.0, 10.0)]  # gap 0 to both
    assert assign_words_to_turns([word], turns)[0].speaker == "A"


# --- empty inputs -----------------------------------------------------------

def test_no_words_returns_empty():
    assert assign_words_to_turns([], [_t("A", 0.0, 1.0)]) == []


def test_no_turns_falls_back_to_single_speaker():
    words = [_w("あ", 0.0, 1.0), _w("い", 1.0, 2.0)]
    out = assign_words_to_turns(words, [])
    assert out == [TranscriptSegment("SPEAKER_00", 0.0, 2.0, "あい")]


def test_no_turns_uses_custom_fallback_speaker():
    out = assign_words_to_turns([_w("あ", 0.0, 1.0)], [], fallback_speaker="S9")
    assert out[0].speaker == "S9"


def test_all_turns_filtered_out_falls_back():
    words = [_w("あ", 0.0, 1.0)]
    turns = [_t("A", 0.0, 0.1)]  # dropped by min_turn_seconds
    out = assign_words_to_turns(words, turns, min_turn_seconds=1.0)
    assert out[0].speaker == "SPEAKER_00"


# --- min_turn_seconds re-attach ---------------------------------------------

def test_short_turn_dropped_words_reattach_to_neighbor():
    words = [_w("x", 5.0, 5.2)]
    turns = [_t("A", 0.0, 4.9), _t("SHORT", 5.0, 5.1), _t("B", 5.3, 9.0)]
    # SHORT (0.1s) is dropped; word re-attaches to nearest survivor.
    out = assign_words_to_turns(words, turns, min_turn_seconds=0.3)
    assert out[0].speaker in ("A", "B")
    assert out[0].speaker != "SHORT"


# --- sanitization: non-finite dropped / reversed clamped --------------------

def test_non_finite_word_timestamps_dropped():
    words = [
        _w("good", 0.0, 1.0),
        _w("nan", math.nan, 2.0),
        _w("inf", 3.0, math.inf),
    ]
    out = assign_words_to_turns(words, [_t("A", 0.0, 5.0)])
    assert len(out) == 1 and out[0].text == "good"


def test_reversed_word_timestamps_are_clamped():
    words = [_w("x", 5.0, 2.0)]  # end < start -> end clamped to 5.0
    out = assign_words_to_turns(words, [_t("A", 0.0, 10.0)])
    assert out[0].start == 5.0 and out[0].end == 5.0


def test_non_finite_turns_dropped():
    words = [_w("あ", 0.0, 1.0)]
    turns = [_t("BAD", math.nan, 1.0), _t("A", 0.0, 2.0)]
    assert assign_words_to_turns(words, turns)[0].speaker == "A"


# --- empty-text filtering ---------------------------------------------------

def test_empty_and_whitespace_words_dropped():
    words = [_w("", 0.0, 1.0), _w("   ", 1.0, 2.0), _w("あ", 2.0, 3.0)]
    out = assign_words_to_turns(words, [_t("A", 0.0, 5.0)])
    assert len(out) == 1 and out[0].text == "あ"


def test_word_text_is_stripped_before_join():
    words = [_w(" こん ", 0.0, 0.5), _w(" にちは ", 0.5, 1.0)]
    out = assign_words_to_turns(words, [_t("A", 0.0, 1.0)])
    assert out[0].text == "こんにちは"


# --- time-order invariant + segment bounds ----------------------------------

def test_unsorted_words_are_sorted_and_output_in_time_order():
    words = [_w("に", 0.5, 1.0), _w("こ", 0.0, 0.5)]
    out = assign_words_to_turns(words, [_t("A", 0.0, 1.0)])
    assert out[0].text == "こに"
    starts = [s.start for s in out]
    assert starts == sorted(starts)


def test_segment_bounds_are_first_start_and_last_end():
    words = [_w("あ", 1.0, 2.0), _w("い", 2.0, 3.5), _w("う", 3.5, 4.0)]
    out = assign_words_to_turns(words, [_t("A", 0.0, 5.0)])
    assert out[0].start == 1.0 and out[0].end == 4.0


def test_multi_speaker_output_is_time_ordered():
    words = [_w("a", 0.0, 1.0), _w("b", 1.0, 2.0), _w("c", 2.0, 3.0)]
    turns = [_t("A", 0.0, 1.0), _t("B", 1.0, 2.0), _t("A", 2.0, 3.0)]
    out = assign_words_to_turns(words, turns)
    assert [s.speaker for s in out] == ["A", "B", "A"]
    assert [s.start for s in out] == [0.0, 1.0, 2.0]


# --- words_from_whisper_result ----------------------------------------------

def test_words_from_whisper_result_normal():
    result = {
        "segments": [
            {"words": [
                {"word": "こん", "start": 0.0, "end": 0.5},
                {"word": "にちは", "start": 0.5, "end": 1.0},
            ]},
        ]
    }
    words = words_from_whisper_result(result)
    assert [w.text for w in words] == ["こん", "にちは"]
    assert words[0].start == 0.0 and words[1].end == 1.0


def test_words_from_whisper_result_fallback_when_no_words():
    result = {"segments": [{"text": "まとめ", "start": 1.0, "end": 2.0}]}
    words = words_from_whisper_result(result)
    assert len(words) == 1
    assert words[0].text == "まとめ" and words[0].start == 1.0


def test_words_from_whisper_result_empty():
    assert words_from_whisper_result({}) == []
    assert words_from_whisper_result({"segments": []}) == []
    assert words_from_whisper_result("not a dict") == []


def test_words_from_whisper_result_end_to_end_with_assign():
    result = {
        "segments": [
            {"words": [
                {"word": "はい", "start": 0.0, "end": 1.0},
                {"word": "そう", "start": 1.0, "end": 2.0},
            ]},
        ]
    }
    words = words_from_whisper_result(result)
    turns = [_t("A", 0.0, 1.0), _t("B", 1.0, 2.0)]
    out = assign_words_to_turns(words, turns)
    assert [(s.speaker, s.text) for s in out] == [("A", "はい"), ("B", "そう")]


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
