from dataclasses import dataclass
from datetime import datetime
from threading import Lock


class JobState:
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


@dataclass
class Job:
    filename: str
    state: str = JobState.QUEUED
    segments_completed: int = 0
    current_text: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    output_path: str | None = None
    error: str | None = None


_STATE_ORDER = {
    JobState.PROCESSING: 0,
    JobState.QUEUED: 1,
    JobState.ERROR: 2,
    JobState.DONE: 3,
}


class StatusStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = Lock()
        self._system_message: str = "starting..."

    def register(self, filename: str) -> Job:
        with self._lock:
            if filename not in self._jobs:
                self._jobs[filename] = Job(filename=filename)
            return self._jobs[filename]

    def update(self, filename: str, **changes) -> None:
        with self._lock:
            job = self._jobs.setdefault(filename, Job(filename=filename))
            for key, value in changes.items():
                setattr(job, key, value)

    def list(self) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        return sorted(jobs, key=lambda j: (_STATE_ORDER.get(j.state, 99), j.filename))

    def get(self, filename: str) -> Job | None:
        with self._lock:
            return self._jobs.get(filename)

    def set_system_message(self, message: str) -> None:
        with self._lock:
            self._system_message = message

    def system_message(self) -> str:
        with self._lock:
            return self._system_message


store = StatusStore()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
