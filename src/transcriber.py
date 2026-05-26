from dataclasses import dataclass
from pathlib import Path

import torch

from .audio import ensure_wav


@dataclass
class TranscriptSegment:
    speaker: str
    start: float
    end: float
    text: str


def diarize_and_transcribe(
    file_path: str | Path,
    whisper_model,
    diarization_pipeline,
    audio_cropper,
    temp_dir: Path,
    num_speakers: int | None = None,
    sample_rate: int = 16000,
    min_segment_seconds: float = 0.3,
) -> list[TranscriptSegment]:
    print(f"Processing: {file_path}")
    wav_path, temporary_file_created = ensure_wav(file_path, temp_dir, sample_rate)

    try:
        diarization = diarization_pipeline(str(wav_path), num_speakers=num_speakers)
        results: list[TranscriptSegment] = []

        for segment, _, speaker in diarization.itertracks(yield_label=True):
            if (segment.end - segment.start) < min_segment_seconds:
                continue

            waveform, _ = audio_cropper.crop(str(wav_path), segment)
            transcription = whisper_model.transcribe(
                waveform.squeeze().numpy(),
                language="ja",
                fp16=torch.cuda.is_available(),
                condition_on_previous_text=False,
            )
            text = (transcription.get("text") or "").strip()
            if not text:
                continue

            row = TranscriptSegment(
                speaker=speaker,
                start=float(segment.start),
                end=float(segment.end),
                text=text,
            )
            results.append(row)
            print(f"[{row.start:.1f}s - {row.end:.1f}s] {row.speaker}: {row.text}")

        return results
    finally:
        if temporary_file_created and wav_path.exists():
            wav_path.unlink()
