import subprocess
from pathlib import Path


def ensure_wav(
    file_path: str | Path,
    temp_dir: Path,
    sample_rate: int = 16000,
) -> tuple[Path, bool]:
    """Convert audio to 16kHz mono WAV if needed.

    Returns (wav_path, created_temp_file).
    """
    file_path = Path(file_path)
    if file_path.suffix.lower() == ".wav":
        return file_path, False

    temp_dir.mkdir(parents=True, exist_ok=True)
    wav_path = temp_dir / (file_path.stem + ".wav")

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(file_path),
            "-ar", str(sample_rate),
            "-ac", "1",
            str(wav_path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return wav_path, True
