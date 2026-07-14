"""Pure, dependency-free word→speaker alignment for ASR-first batch transcription.

No torch / whisper / pyannote / config imports — stdlib only — so the alignment
logic is unit-testable without a GPU, audio, or models. The batch transcriber
(src/transcriber.py) runs one whole-file whisper pass (word timestamps) and one
whole-file pyannote diarization, then calls assign_words_to_turns() to map each
recognized word to the speaker turn it best overlaps and to group consecutive
same-speaker words into TranscriptSegments (no-space join for Japanese).
"""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Turn:
    speaker: str
    start: float
    end: float


@dataclass
class TranscriptSegment:
    speaker: str
    start: float
    end: float
    text: str


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def words_from_whisper_result(result: dict) -> list[Word]:
    """Normalize a raw openai-whisper transcribe() dict into a list of Word.

    Iterates result["segments"]; for each segment prefers its per-word
    timestamps (seg["words"], items shaped {"word", "start", "end"}). If a
    segment carries no usable word list (defensive — some paths omit them), it
    falls back to a single pseudo-word from the segment's own text/start/end.
    Timestamp sanitation is NOT done here (assign_words_to_turns owns that);
    unparseable timestamps become NaN and are dropped downstream.
    """
    words: list[Word] = []
    if not isinstance(result, dict):
        return words
    for seg in result.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        appended = False
        for w in seg.get("words") or []:
            if not isinstance(w, dict):
                continue
            words.append(
                Word(
                    text=str(w.get("word", "")),
                    start=_as_float(w.get("start")),
                    end=_as_float(w.get("end")),
                )
            )
            appended = True
        if not appended and str(seg.get("text", "")).strip():
            words.append(
                Word(
                    text=str(seg.get("text", "")),
                    start=_as_float(seg.get("start")),
                    end=_as_float(seg.get("end")),
                )
            )
    return words


def _sanitize_words(words: list[Word]) -> list[Word]:
    cleaned: list[Word] = []
    for w in words:
        text = w.text.strip()
        if not text:
            continue  # drop empty-after-strip text
        if not (math.isfinite(w.start) and math.isfinite(w.end)):
            continue  # drop non-finite timestamps
        end = w.end if w.end >= w.start else w.start  # clamp reversed
        cleaned.append(Word(text=text, start=w.start, end=end))
    cleaned.sort(key=lambda w: (w.start, w.end))  # stable
    return cleaned


def _filter_turns(turns: list[Turn], min_turn_seconds: float) -> list[Turn]:
    cleaned = [
        t
        for t in turns
        if math.isfinite(t.start)
        and math.isfinite(t.end)
        and (t.end - t.start) >= min_turn_seconds
    ]
    cleaned.sort(key=lambda t: (t.start, t.end, t.speaker))
    return cleaned


def _best_turn(word: Word, turns: list[Turn]) -> Turn:
    best_index = 0
    best_key = None
    for index, t in enumerate(turns):
        overlap = max(0.0, min(word.end, t.end) - max(word.start, t.start))
        gap = max(0.0, t.start - word.end, word.start - t.end)
        key = (overlap, -gap, -index)  # max overlap -> min gap -> earliest turn
        if best_key is None or key > best_key:
            best_key = key
            best_index = index
    return turns[best_index]


def _group(words: list[Word], speakers: list[str]) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    run_speaker: str | None = None
    run: list[Word] = []

    def flush() -> None:
        if not run:
            return
        text = "".join(w.text for w in run)  # no-space join (Japanese)
        if text:  # drop empty-text groups (defensive)
            segments.append(
                TranscriptSegment(
                    speaker=run_speaker,
                    start=run[0].start,
                    end=run[-1].end,
                    text=text,
                )
            )

    for word, speaker in zip(words, speakers):
        if run and speaker != run_speaker:
            flush()
            run = []
        run_speaker = speaker
        run.append(word)
    flush()
    return segments


def assign_words_to_turns(
    words: list[Word],
    turns: list[Turn],
    *,
    min_turn_seconds: float = 0.0,
    fallback_speaker: str = "SPEAKER_00",
) -> list[TranscriptSegment]:
    """Assign each word to its best speaker turn, then group same-speaker runs.

    Pure and deterministic:
      1. Sanitize words (strip+drop empty text, drop non-finite, clamp reversed,
         stable sort by (start, end)).
      2. Filter turns (finite, duration >= min_turn_seconds; sort by
         (start, end, speaker)).
      3. No surviving words -> []. No surviving turns -> every word labelled
         fallback_speaker (single-speaker meeting).
      4. Per word, pick the turn maximizing (overlap, -gap, -index): max overlap
         wins; on zero overlap the nearest turn by gap wins; ties -> earliest
         turn in sort order.
      5. Group consecutive same-speaker words into TranscriptSegments (start =
         first word start, end = last word end, no-space text join); drop
         empty-text groups. Output is naturally in time order.
    """
    clean_words = _sanitize_words(words)
    if not clean_words:
        return []
    clean_turns = _filter_turns(turns, min_turn_seconds)
    if not clean_turns:
        speakers = [fallback_speaker] * len(clean_words)
    else:
        speakers = [_best_turn(w, clean_turns).speaker for w in clean_words]
    return _group(clean_words, speakers)
