"""Startup recovery of orphaned live recordings (``*.wav.part``).

Live sessions record to ``tmp_audio/live_<id>.wav.part`` and atomically
rename to ``.wav`` only on graceful finalize (see ``session.py``). If the
process is killed mid-recording (desktop shell crash, hard shutdown), a
``.wav.part`` is left behind whose RIFF/data chunk sizes are stale — the
stdlib ``wave`` module only patches the header on ``close()``.

``recover_orphaned_wavs`` runs once at startup (called from ``main.py``):
- parses each ``*.wav.part`` in the temp dir,
- recomputes the real data length from the file size, rewrites the RIFF and
  ``data`` chunk size fields in place (trimming any partial trailing frame),
- moves salvageable audio to ``SOURCE_DIR/recovered_<name>.wav`` so the
  existing batch pipeline ingests it as a normal job,
- deletes unsalvageable leftovers (unparseable header or < 1 s of audio).

Stdlib-only (struct + shutil); never raises for a single bad file — a
corrupt leftover must not block startup.
"""
from __future__ import annotations

import shutil
import struct
from dataclasses import dataclass
from pathlib import Path

MIN_RECOVER_SECONDS = 1.0
RECOVERED_PREFIX = "recovered_"
_PART_SUFFIX = ".part"
_RIFF_HEADER_LEN = 12
_CHUNK_HEADER_LEN = 8
_FMT_CHUNK_MIN_LEN = 16
_MAX_CHUNKS = 64  # sanity cap against corrupt chunk lists


@dataclass(frozen=True)
class _WavLayout:
    """Byte layout of a (possibly truncated) RIFF/WAVE file."""

    channels: int
    sample_rate: int
    sample_width: int
    data_payload_offset: int  # first byte of PCM payload

    @property
    def frame_size(self) -> int:
        return self.channels * self.sample_width


def recover_orphaned_wavs(temp_dir: Path, source_dir: Path) -> list[Path]:
    """Repair and re-ingest orphaned ``*.wav.part`` files. Returns the moves."""
    recovered: list[Path] = []
    if not temp_dir.is_dir():
        return recovered
    for part in sorted(temp_dir.glob(f"*.wav{_PART_SUFFIX}")):
        try:
            if not _repair_wav_part(part):
                part.unlink(missing_ok=True)
                print(f"Live recovery: discarded unsalvageable {part.name}")
                continue
            source_dir.mkdir(parents=True, exist_ok=True)
            target = _unique_recovered_target(source_dir, part.name)
            shutil.move(str(part), str(target))
            recovered.append(target)
            print(f"Live recovery: {part.name} -> {target}")
        except Exception as error:  # never block startup on one bad file
            print(f"Live recovery failed for {part.name}: {error}")
    return recovered


def _unique_recovered_target(source_dir: Path, part_name: str) -> Path:
    stem = part_name[: -len(_PART_SUFFIX)]  # live_<id>.wav.part -> live_<id>.wav
    target = source_dir / f"{RECOVERED_PREFIX}{stem}"
    counter = 2
    while target.exists():
        target = source_dir / f"{RECOVERED_PREFIX}{Path(stem).stem}_{counter}.wav"
        counter += 1
    return target


def _repair_wav_part(part: Path) -> bool:
    """Fix the RIFF/data sizes in place. False when not worth keeping."""
    file_size = part.stat().st_size
    layout = _parse_wav_layout(part, file_size)
    if layout is None or layout.frame_size <= 0 or layout.sample_rate <= 0:
        return False

    data_bytes = file_size - layout.data_payload_offset
    data_bytes -= data_bytes % layout.frame_size  # drop a partial trailing frame
    seconds = data_bytes / (layout.sample_rate * layout.frame_size)
    if seconds < MIN_RECOVER_SECONDS:
        return False

    riff_size = layout.data_payload_offset + data_bytes - _CHUNK_HEADER_LEN
    with part.open("r+b") as handle:
        handle.seek(4)
        handle.write(struct.pack("<I", riff_size))
        handle.seek(layout.data_payload_offset - 4)
        handle.write(struct.pack("<I", data_bytes))
        handle.truncate(layout.data_payload_offset + data_bytes)
    return True


def _parse_wav_layout(part: Path, file_size: int) -> _WavLayout | None:
    """Walk the chunk list to locate ``fmt `` and ``data`` (sizes untrusted)."""
    with part.open("rb") as handle:
        header = handle.read(_RIFF_HEADER_LEN)
        if len(header) < _RIFF_HEADER_LEN:
            return None
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            return None

        channels = sample_rate = sample_width = 0
        offset = _RIFF_HEADER_LEN
        for _ in range(_MAX_CHUNKS):
            handle.seek(offset)
            chunk_header = handle.read(_CHUNK_HEADER_LEN)
            if len(chunk_header) < _CHUNK_HEADER_LEN:
                return None
            chunk_id = chunk_header[:4]
            (chunk_size,) = struct.unpack("<I", chunk_header[4:])
            payload_offset = offset + _CHUNK_HEADER_LEN

            if chunk_id == b"fmt ":
                fmt = handle.read(_FMT_CHUNK_MIN_LEN)
                if len(fmt) < _FMT_CHUNK_MIN_LEN:
                    return None
                _fmt_tag, channels, sample_rate = struct.unpack("<HHI", fmt[:8])
                (bits_per_sample,) = struct.unpack("<H", fmt[14:16])
                sample_width = bits_per_sample // 8
            elif chunk_id == b"data":
                if not (channels and sample_rate and sample_width):
                    return None
                return _WavLayout(
                    channels=channels,
                    sample_rate=sample_rate,
                    sample_width=sample_width,
                    data_payload_offset=payload_offset,
                )

            # Skip to the next chunk (chunk sizes are padded to even length).
            offset = payload_offset + chunk_size + (chunk_size % 2)
            if offset >= file_size:
                return None
    return None
