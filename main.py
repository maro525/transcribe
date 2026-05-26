from pathlib import Path

from src import config
from src.auth import load_hf_token
from src.formatter import save_transcript
from src.models import (
    get_device,
    load_diarization_pipeline,
    load_whisper_model,
    make_audio_cropper,
)
from src.transcriber import diarize_and_transcribe
from src.watcher import move_to_done, watch_folder


def main() -> None:
    config.ensure_directories()

    hf_token = load_hf_token(config.ENV_FILE)
    device = get_device()
    print("Using device:", device)
    print("Base dir:", config.BASE_DIR)

    whisper_model = load_whisper_model(config.WHISPER_MODEL)
    diarization_pipeline = load_diarization_pipeline(
        hf_token=hf_token,
        cache_dir=config.CACHE_DIR,
        model_name=config.DIARIZATION_MODEL,
        device=device,
    )
    audio_cropper = make_audio_cropper(config.SAMPLE_RATE)

    print("Expected speakers:", config.NUM_SPEAKERS)

    def process_file(file_path: Path) -> None:
        segments = diarize_and_transcribe(
            file_path,
            whisper_model=whisper_model,
            diarization_pipeline=diarization_pipeline,
            audio_cropper=audio_cropper,
            temp_dir=config.TEMP_DIR,
            num_speakers=config.NUM_SPEAKERS,
            sample_rate=config.SAMPLE_RATE,
            min_segment_seconds=config.MIN_SEGMENT_SECONDS,
        )
        save_transcript(segments, file_path.name, config.OUTPUT_DIR)
        move_to_done(file_path, config.DONE_DIR)

    watch_folder(
        source_dir=config.SOURCE_DIR,
        process_file=process_file,
        extensions=config.SUPPORTED_EXTENSIONS,
        interval_seconds=config.WATCH_INTERVAL_SECONDS,
    )


if __name__ == "__main__":
    main()
