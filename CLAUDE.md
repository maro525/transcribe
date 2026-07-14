# Current Project: NSKETCH-885 — ASR-first batch transcription (whisperX-style)

## Goal
- Batch pipeline: one whole-file whisper pass (word_timestamps=True, ja) + whole-file pyannote diarization; pure word→turn max-overlap assignment + same-speaker grouping (no-space join). Public contract (list[TranscriptSegment] + on_segment time order) unchanged.

## Key files
- `src/align.py` — stdlib-only pure alignment: Word/Turn/TranscriptSegment, words_from_whisper_result, assign_words_to_turns.
- `src/transcriber.py` — whole-file decode + diarization + alignment; re-exports TranscriptSegment; signature unchanged (audio_cropper unused).
- `tests/test_align.py` — pure alignment matrix, no torch/GPU/audio.

## Architecture
- ASR-first (whisperX-style); assignment: max overlap → min gap → earliest turn; min_segment_seconds now drops short *turns* pre-assignment (words re-attach to neighbors).
- worker.py / artifacts.py / formatter.py / discourse untouched; BATCH_WHISPER_MODE resolution (NSKETCH-883) untouched.

## Decisions
- Word-level granularity; condition_on_previous_text=False default + BATCH_CONDITION_ON_PREVIOUS_TEXT knob.
- on_segment emitted post-alignment in time order (no incremental emission).
- NaN/inf words dropped, reversed timestamps clamped (never fail the job); empty diarization → SPEAKER_00.
