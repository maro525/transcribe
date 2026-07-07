from pathlib import Path

from . import config
from .artifacts import save_artifacts
from .auth import load_hf_token
from .formatter import save_transcript
from .live import state as live_state
from .models import (
    get_audio_cropper,
    get_device,
    get_diarization_pipeline,
    get_whisper_model,
)
from .status import JobState, now_iso, store
from .transcriber import diarize_and_transcribe
from .watcher import move_to_done, watch_folder


def bootstrap_history() -> None:
    """Populate the dashboard with previously completed jobs on the disk."""
    for output_file in config.OUTPUT_DIR.glob("*.txt"):
        for extension in config.SUPPORTED_EXTENSIONS:
            audio_path = config.DONE_DIR / (output_file.stem + extension)
            if audio_path.exists():
                store.update(
                    audio_path.name,
                    state=JobState.DONE,
                    output_path=str(output_file),
                )
                break


def run() -> None:
    """Long-running worker. Loads models, then watches the input folder."""
    try:
        store.set_system_message("loading models...")
        hf_token = load_hf_token(config.ENV_FILE)
        device = get_device()
        print("Using device:", device)
        print("Base dir:", config.BASE_DIR)

        whisper_model = get_whisper_model(config.WHISPER_MODEL)
        diarization_pipeline = get_diarization_pipeline(
            hf_token=hf_token,
            cache_dir=config.CACHE_DIR,
            model_name=config.DIARIZATION_MODEL,
            device=device,
        )
        audio_cropper = get_audio_cropper(config.SAMPLE_RATE)
        store.set_system_message(
            f"ready (device: {device}, whisper: {config.WHISPER_MODEL})"
        )
    except Exception as error:
        store.set_system_message(f"startup failed: {error}")
        raise

    def on_discover(file_path: Path) -> None:
        store.register(file_path.name)

    def process_file(file_path: Path) -> None:
        store.update(
            file_path.name,
            state=JobState.PROCESSING,
            started_at=now_iso(),
            error=None,
            segments_completed=0,
            current_text="",
        )
        try:
            count = {"n": 0}

            def on_segment(segment) -> None:
                count["n"] += 1
                store.update(
                    file_path.name,
                    segments_completed=count["n"],
                    current_text=f"[{segment.speaker}] {segment.text}",
                )

            segments = diarize_and_transcribe(
                file_path,
                whisper_model=whisper_model,
                diarization_pipeline=diarization_pipeline,
                audio_cropper=audio_cropper,
                temp_dir=config.TEMP_DIR,
                num_speakers=config.NUM_SPEAKERS,
                sample_rate=config.SAMPLE_RATE,
                min_segment_seconds=config.MIN_SEGMENT_SECONDS,
                on_segment=on_segment,
            )
            output_path = save_transcript(segments, file_path.name, config.OUTPUT_DIR)
            try:
                save_artifacts(
                    config.OUTPUT_DIR,
                    file_path.name,
                    [segment.text for segment in segments],
                )
            except Exception as error:  # best-effort; never fail the job
                print(f"Artifact generation failed for {file_path.name}: {error}")
            move_to_done(file_path, config.DONE_DIR)
            store.update(
                file_path.name,
                state=JobState.DONE,
                finished_at=now_iso(),
                output_path=str(output_path) if output_path else None,
            )
        except Exception as error:
            store.update(
                file_path.name,
                state=JobState.ERROR,
                finished_at=now_iso(),
                error=str(error),
            )
            print(f"Error processing {file_path.name}: {error}")

    watch_folder(
        source_dir=config.SOURCE_DIR,
        process_file=process_file,
        extensions=config.SUPPORTED_EXTENSIONS,
        interval_seconds=config.WATCH_INTERVAL_SECONDS,
        on_discover=on_discover,
        should_pause=live_state.is_live_active,
    )
