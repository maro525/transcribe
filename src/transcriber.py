from pathlib import Path
from typing import Callable

import torch

from . import config
from .align import (
    TranscriptSegment,
    Turn,
    assign_words_to_turns,
    words_from_whisper_result,
)
from .audio import ensure_wav

__all__ = ["TranscriptSegment", "diarize_and_transcribe"]


def diarize_and_transcribe(
    file_path: str | Path,
    whisper_model,
    diarization_pipeline,
    audio_cropper,  # accepted-but-unused: ASR-first decodes the whole file, not
    temp_dir: Path,  # per-turn crops. Kept so worker.py stays unchanged (NSKETCH-885).
    num_speakers: int | None = None,
    sample_rate: int = 16000,
    min_segment_seconds: float = 0.3,
    on_segment: Callable[[TranscriptSegment], None] | None = None,
) -> list[TranscriptSegment]:
    """ASR-first (whisperX-style) batch transcription.

    One whole-file whisper pass with word timestamps + one whole-file pyannote
    diarization, then each word is assigned to its max-overlap speaker turn
    (nearest turn on zero overlap) and consecutive same-speaker words are grouped
    into TranscriptSegments. Public contract is unchanged: returns
    list[TranscriptSegment] in time order and fires on_segment once per segment
    in time order. ``audio_cropper`` is accepted for signature compatibility but
    unused in ASR-first mode. ``diarization_pipeline`` may be None (HF token
    not configured): diarization is then skipped and every word falls back to
    the single fallback speaker.
    """
    print(f"Processing: {file_path}")
    wav_path, temporary_file_created = ensure_wav(file_path, temp_dir, sample_rate)

    try:
        transcription = whisper_model.transcribe(
            str(wav_path),
            language="ja",
            word_timestamps=True,
            fp16=torch.cuda.is_available(),
            condition_on_previous_text=config.BATCH_CONDITION_ON_PREVIOUS_TEXT,
        )
        words = words_from_whisper_result(transcription)

        if diarization_pipeline is None:
            # No HF token (diarization disabled): with no turns,
            # assign_words_to_turns labels every word with its fallback
            # speaker — single-speaker output.
            turns = []
        else:
            diarization = diarization_pipeline(str(wav_path), num_speakers=num_speakers)
            turns = [
                Turn(speaker=speaker, start=float(segment.start), end=float(segment.end))
                for segment, _, speaker in diarization.itertracks(yield_label=True)
            ]

        results = assign_words_to_turns(
            words, turns, min_turn_seconds=min_segment_seconds
        )

        for row in results:
            print(f"[{row.start:.1f}s - {row.end:.1f}s] {row.speaker}: {row.text}")
            if on_segment is not None:
                on_segment(row)

        return results
    finally:
        if temporary_file_created and wav_path.exists():
            wav_path.unlink()
