import os
import shutil
import time
from pathlib import Path
from typing import Callable


def move_to_done(file_path: Path, done_dir: Path) -> Path:
    done_dir.mkdir(parents=True, exist_ok=True)
    destination = done_dir / file_path.name
    shutil.move(str(file_path), str(destination))
    return destination


def watch_folder(
    source_dir: Path,
    process_file: Callable[[Path], None],
    extensions: tuple[str, ...] = (".mp3", ".wav", ".m4a"),
    interval_seconds: int = 30,
    on_discover: Callable[[Path], None] | None = None,
) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    print("Watching folder:", source_dir)

    while True:
        files = [
            source_dir / name
            for name in os.listdir(source_dir)
            if name.lower().endswith(extensions)
        ]

        if on_discover is not None:
            for file_path in files:
                on_discover(file_path)

        if not files:
            print(f"No new files. Waiting {interval_seconds} seconds...")
            time.sleep(interval_seconds)
            continue

        for file_path in files:
            try:
                process_file(file_path)
            except Exception as error:
                print(f"Error processing {file_path.name}: {error}")

        print(f"Cycle complete. Waiting {interval_seconds} seconds...")
        time.sleep(interval_seconds)
