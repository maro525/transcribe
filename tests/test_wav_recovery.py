"""Unit tests for src/live/recovery.py (truncated-WAV fixtures, stdlib only)."""
import struct
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.live.recovery import (  # noqa: E402
    MIN_RECOVER_SECONDS,
    recover_orphaned_wavs,
)

SR = 16000
_STALE_RIFF_SIZE = 36  # what an unclosed header typically declares
_HEADER_LEN = 44


def _wav_header(
    sample_rate: int = SR,
    channels: int = 1,
    sample_width: int = 2,
    declared_data_size: int = 0,
) -> bytes:
    """A canonical 44-byte header with stale (pre-close) size fields."""
    block_align = channels * sample_width
    return (
        b"RIFF"
        + struct.pack("<I", _STALE_RIFF_SIZE)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + struct.pack(
            "<HHIIHH",
            1,  # PCM
            channels,
            sample_rate,
            sample_rate * block_align,
            block_align,
            sample_width * 8,
        )
        + b"data"
        + struct.pack("<I", declared_data_size)
    )


def _write_part(path: Path, seconds: float, trailing_garbage: int = 0) -> int:
    """A truncated .wav.part: stale header + PCM cut off mid-stream."""
    frames = int(seconds * SR)
    pcm = b"\x34\x12" * frames
    path.write_bytes(_wav_header() + pcm + b"\x9c" * trailing_garbage)
    return frames


def test_salvageable_part_is_repaired_and_moved_to_input():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        temp_dir, source_dir = base / "tmp_audio", base / "input"
        temp_dir.mkdir()
        part = temp_dir / "live_20260726_0101.wav.part"
        frames = _write_part(part, seconds=2.0)

        recovered = recover_orphaned_wavs(temp_dir, source_dir)

        target = source_dir / "recovered_live_20260726_0101.wav"
        assert recovered == [target]
        assert not part.exists()
        # The repaired header must make the file readable by the stdlib wave
        # module with every frame accounted for.
        with wave.open(str(target), "rb") as handle:
            assert handle.getnchannels() == 1
            assert handle.getframerate() == SR
            assert handle.getsampwidth() == 2
            assert handle.getnframes() == frames


def test_recovered_name_lands_in_batch_watch_pattern():
    # The batch watcher only picks up SUPPORTED_EXTENSIONS — the recovered
    # file must end in .wav (not .wav.part).
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        temp_dir, source_dir = base / "t", base / "i"
        temp_dir.mkdir()
        _write_part(temp_dir / "live_x.wav.part", seconds=1.5)
        recovered = recover_orphaned_wavs(temp_dir, source_dir)
        assert [p.suffix for p in recovered] == [".wav"]


def test_partial_trailing_frame_is_trimmed():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        temp_dir, source_dir = base / "t", base / "i"
        temp_dir.mkdir()
        part = temp_dir / "live_x.wav.part"
        frames = _write_part(part, seconds=1.2, trailing_garbage=1)  # odd byte

        recovered = recover_orphaned_wavs(temp_dir, source_dir)

        assert len(recovered) == 1
        with wave.open(str(recovered[0]), "rb") as handle:
            assert handle.getnframes() == frames  # garbage byte dropped


def test_too_short_audio_is_deleted():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        temp_dir, source_dir = base / "t", base / "i"
        temp_dir.mkdir()
        part = temp_dir / "live_short.wav.part"
        _write_part(part, seconds=MIN_RECOVER_SECONDS / 2)

        recovered = recover_orphaned_wavs(temp_dir, source_dir)

        assert recovered == []
        assert not part.exists()
        assert not source_dir.exists() or not list(source_dir.iterdir())


def test_unparseable_leftover_is_deleted():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        temp_dir, source_dir = base / "t", base / "i"
        temp_dir.mkdir()
        part = temp_dir / "live_junk.wav.part"
        part.write_bytes(b"not a riff file at all" * 100)

        recovered = recover_orphaned_wavs(temp_dir, source_dir)

        assert recovered == []
        assert not part.exists()


def test_header_only_part_is_deleted():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        temp_dir, source_dir = base / "t", base / "i"
        temp_dir.mkdir()
        part = temp_dir / "live_empty.wav.part"
        part.write_bytes(_wav_header())

        assert recover_orphaned_wavs(temp_dir, source_dir) == []
        assert not part.exists()


def test_wave_module_truncation_shape_is_recovered():
    """Realistic fixture: a file written by the wave module whose header was
    never patched by close() (process killed mid-recording)."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        temp_dir, source_dir = base / "t", base / "i"
        temp_dir.mkdir()
        part = temp_dir / "live_killed.wav.part"

        handle = wave.open(str(part), "wb")
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframesraw(b"\x00\x10" * SR * 3)  # 3 s, header NOT patched
        handle._file.flush()  # simulate the crash: no close()
        handle._file.close()
        handle._file = None  # keep __del__ from patching the header
        del handle

        recovered = recover_orphaned_wavs(temp_dir, source_dir)

        assert len(recovered) == 1
        with wave.open(str(recovered[0]), "rb") as reader:
            assert reader.getnframes() == SR * 3


def test_name_collision_gets_counter_suffix():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        temp_dir, source_dir = base / "t", base / "i"
        temp_dir.mkdir()
        source_dir.mkdir()
        (source_dir / "recovered_live_x.wav").write_bytes(b"existing")
        _write_part(temp_dir / "live_x.wav.part", seconds=1.5)

        recovered = recover_orphaned_wavs(temp_dir, source_dir)

        assert recovered == [source_dir / "recovered_live_x_2.wav"]


def test_multiple_parts_processed_independently():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        temp_dir, source_dir = base / "t", base / "i"
        temp_dir.mkdir()
        _write_part(temp_dir / "live_a.wav.part", seconds=2.0)
        (temp_dir / "live_b.wav.part").write_bytes(b"garbage")
        _write_part(temp_dir / "live_c.wav.part", seconds=1.5)

        recovered = recover_orphaned_wavs(temp_dir, source_dir)

        assert sorted(p.name for p in recovered) == [
            "recovered_live_a.wav",
            "recovered_live_c.wav",
        ]
        assert not list(temp_dir.glob("*.part"))


def test_missing_temp_dir_is_a_noop():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        assert recover_orphaned_wavs(base / "nope", base / "i") == []


def test_finished_wavs_in_temp_are_left_alone():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        temp_dir, source_dir = base / "t", base / "i"
        temp_dir.mkdir()
        keeper = temp_dir / "live_done.wav"  # no .part suffix — not orphaned
        keeper.write_bytes(_wav_header() + b"\x00\x00" * SR * 2)

        assert recover_orphaned_wavs(temp_dir, source_dir) == []
        assert keeper.exists()


if __name__ == "__main__":
    from _runner import run_module

    run_module(globals())
