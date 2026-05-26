from pathlib import Path

from .transcriber import TranscriptSegment


def format_timestamp(seconds: float) -> str:
    total_milliseconds = int(round(float(seconds) * 1000))
    hours, remainder = divmod(total_milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    secs, milliseconds = divmod(remainder, 1000)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"
    return f"{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def save_transcript(
    segments: list[TranscriptSegment],
    src_filename: str,
    output_dir: Path,
) -> Path | None:
    if not segments:
        print(f"No transcript rows produced: {src_filename}")
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    name = Path(src_filename).stem
    output_path = output_dir / (name + ".txt")

    with output_path.open("w", encoding="utf-8") as file_handle:
        for segment in segments:
            start_label = format_timestamp(segment.start)
            end_label = format_timestamp(segment.end)
            file_handle.write(
                f"[{start_label} - {end_label}] {segment.speaker}: {segment.text}\n"
            )

    print(f"Saved: {output_path}")
    return output_path
