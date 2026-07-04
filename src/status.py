from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from queue import Empty, Full, Queue
from threading import Lock
from typing import Any


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


@dataclass(frozen=True)
class StoreEvent:
    id: int
    event: str
    data: dict[str, Any]


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
        self._version = 0
        self._subscribers: set[Queue[StoreEvent]] = set()

    def register(self, filename: str) -> Job:
        event: StoreEvent | None = None
        subscribers: list[Queue[StoreEvent]] = []
        with self._lock:
            if filename not in self._jobs:
                self._jobs[filename] = Job(filename=filename)
                event, subscribers = self._make_event_locked(
                    "job_registered", {"job": self._job_to_dict(self._jobs[filename])}
                )
            job = self._jobs[filename]
        if event:
            self._publish(subscribers, event)
        return job

    def update(self, filename: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs.setdefault(filename, Job(filename=filename))
            for key, value in changes.items():
                setattr(job, key, value)
            event, subscribers = self._make_event_locked(
                "job_updated", {"job": self._job_to_dict(job)}
            )
        self._publish(subscribers, event)

    def list(self) -> list[Job]:
        with self._lock:
            return self._sorted_jobs_locked()

    def get(self, filename: str) -> Job | None:
        with self._lock:
            return self._jobs.get(filename)

    def set_system_message(self, message: str) -> None:
        with self._lock:
            self._system_message = message
            event, subscribers = self._make_event_locked(
                "system_message", {"system_message": self._system_message}
            )
        self._publish(subscribers, event)

    def system_message(self) -> str:
        with self._lock:
            return self._system_message

    def snapshot(self) -> dict[str, Any]:
        snapshot, _version = self.snapshot_with_version()
        return snapshot

    def snapshot_with_version(self) -> tuple[dict[str, Any], int]:
        with self._lock:
            return {
                "system_message": self._system_message,
                "jobs": [self._job_to_dict(job) for job in self._sorted_jobs_locked()],
            }, self._version

    def subscribe(self, maxsize: int = 100) -> Queue[StoreEvent]:
        subscriber, _snapshot, _version = self.subscribe_with_snapshot(maxsize=maxsize)
        return subscriber

    def subscribe_with_snapshot(
        self, maxsize: int = 100
    ) -> tuple[Queue[StoreEvent], dict[str, Any], int]:
        subscriber: Queue[StoreEvent] = Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.add(subscriber)
            snapshot = {
                "system_message": self._system_message,
                "jobs": [self._job_to_dict(job) for job in self._sorted_jobs_locked()],
            }
            return subscriber, snapshot, self._version

    def unsubscribe(self, subscriber: Queue[StoreEvent]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def _sorted_jobs_locked(self) -> list[Job]:
        return sorted(
            self._jobs.values(), key=lambda j: (_STATE_ORDER.get(j.state, 99), j.filename)
        )

    def _make_event_locked(
        self, event_type: str, data: dict[str, Any]
    ) -> tuple[StoreEvent, list[Queue[StoreEvent]]]:
        self._version += 1
        return StoreEvent(self._version, event_type, data), list(self._subscribers)

    def _publish(
        self, subscribers: list[Queue[StoreEvent]], event: StoreEvent
    ) -> None:
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except Full:
                self._enqueue_reset_for_overflow(subscriber, event.id)

    @staticmethod
    def _enqueue_reset_for_overflow(
        subscriber: Queue[StoreEvent], event_id: int
    ) -> None:
        while True:
            try:
                subscriber.get_nowait()
            except Empty:
                break
        try:
            subscriber.put_nowait(
                StoreEvent(
                    event_id,
                    "reset",
                    {"reason": "subscriber_queue_overflow"},
                )
            )
        except Full:
            pass

    @staticmethod
    def _job_to_dict(job: Job) -> dict[str, Any]:
        return {
            "filename": job.filename,
            "state": job.state,
            "segments_completed": job.segments_completed,
            "current_text": job.current_text,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "output_path": job.output_path,
            "error": job.error,
        }


store = StatusStore()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
