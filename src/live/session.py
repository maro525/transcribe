"""LiveSessionManager: the hub of the realtime transcription mode.

State machine: idle -> recording -> finalizing -> idle. A single session at
a time (a second `start` is rejected).

Responsibilities:
- receive 16 kHz/mono/Int16 PCM, append it to a WAV in tmp_audio/,
  and feed it through VAD -> transcription worker;
- broadcast protocol messages (status/partial/final/keywords/graph/finalized/error)
  to registered listeners (the WebSocket handlers);
- keep a ring buffer of recent finals for reconnect recovery;
- append finals to a live-draft text file in output/;
- on finalize, move the WAV into input/ so the existing batch pipeline
  (whisper + pyannote diarization) picks it up as a normal job;
- pause the batch worker while a session is active (GPU coordination).
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import wave
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .. import config
from . import state as live_state
from .graph import CooccurrenceGraph
from .streaming import TranscriptionWorker
from .terms import extract_terms
from .vad import SileroVad, UtteranceSegmenter

Listener = Callable[[dict[str, Any]], None]

_INT16_SCALE = 32768.0

# In-progress recordings carry this suffix (live_<id>.wav.part) and are
# atomically renamed to .wav on graceful finalize; leftovers are repaired at
# startup by src/live/recovery.py.
PART_SUFFIX = ".part"

# Audio source captured natively by the desktop (Tauri) shell. Transitions
# for this source are announced on stdout as TAURI_EVENT lines so the Rust
# side (which drains our stdout) can start/stop the WASAPI loopback capture.
SYSTEM_SOURCE = "system"


def _emit_tauri_event(capture: str, session_id: str) -> None:
    """One TAURI_EVENT line on stdout (frozen contract with the Rust shell)."""
    payload = json.dumps(
        {"capture": capture, "session_id": session_id}, ensure_ascii=False
    )
    print(f"TAURI_EVENT {payload}", flush=True)


class LiveSessionError(RuntimeError):
    """User-facing session errors (already running, not recording, ...)."""


class LiveSessionManager:
    def __init__(
        self,
        *,
        source_dir: Path | None = None,
        output_dir: Path | None = None,
        temp_dir: Path | None = None,
        done_dir: Path | None = None,
        engine_provider: Callable[[], Any] | None = None,
        vad_factory: Callable[[], Any] | None = None,
        history_size: int = config.LIVE_FINAL_HISTORY_SIZE,
        graph_max_nodes: int = config.LIVE_GRAPH_MAX_NODES,
        disconnect_finalize_seconds: float = config.LIVE_DISCONNECT_FINALIZE_SECONDS,
    ) -> None:
        self._source_dir = source_dir or config.SOURCE_DIR
        self._output_dir = output_dir or config.OUTPUT_DIR
        self._temp_dir = temp_dir or config.TEMP_DIR
        self._done_dir = done_dir or config.DONE_DIR
        self._engine_provider = engine_provider or _default_engine_provider
        self._vad_factory = vad_factory or SileroVad
        self._disconnect_finalize_seconds = disconnect_finalize_seconds

        self._lock = threading.RLock()
        self._listeners: list[Listener] = []
        self._state = "idle"
        self._session_id: str | None = None
        self._source: str | None = None
        self._samples_received = 0
        self._wav: wave.Wave_write | None = None
        self._wav_path: Path | None = None
        self._segmenter: UtteranceSegmenter | None = None
        self._worker: TranscriptionWorker | None = None
        self._final_history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._final_texts: list[str] = []
        self._graph = CooccurrenceGraph(
            graph_max_nodes,
            decay=config.LIVE_GRAPH_DECAY,
            min_salience=config.LIVE_GRAPH_MIN_SALIENCE,
            min_frequency=config.LIVE_GRAPH_MIN_FREQUENCY,
            max_candidates=config.LIVE_GRAPH_MAX_CANDIDATES,
        )
        self._draft_path: Path | None = None
        self._clients = 0
        self._disconnect_timer: threading.Timer | None = None

    # --- public API -------------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "type": "status",
                "state": self._state,
                "session_id": self._session_id,
                "source": self._source,
                "elapsed_sec": round(self._samples_received / config.SAMPLE_RATE, 1),
            }

    def start(self, source: str = "mic") -> None:
        """Begin a session. Raises LiveSessionError if one is active."""
        with self._lock:
            if self._state != "idle":
                raise LiveSessionError("live session already active")

            session_id = self._unique_session_id(datetime.now())
            self._temp_dir.mkdir(parents=True, exist_ok=True)
            wav_path = self._temp_dir / f"live_{session_id}.wav{PART_SUFFIX}"

            try:
                vad = self._vad_factory()
            except Exception as error:
                raise LiveSessionError(f"VAD init failed: {error}") from error

            wav = wave.open(str(wav_path), "wb")
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(config.SAMPLE_RATE)

            try:
                worker = TranscriptionWorker(
                    self._engine_provider, self._make_emit(session_id)
                )
                segmenter = UtteranceSegmenter(vad.probability)
            except Exception as error:
                wav.close()
                wav_path.unlink(missing_ok=True)
                raise LiveSessionError(f"live session init failed: {error}") from error

            self._state = "recording"
            self._session_id = session_id
            self._source = source
            self._samples_received = 0
            self._wav = wav
            self._wav_path = wav_path
            self._segmenter = segmenter
            self._worker = worker
            self._final_history.clear()
            self._final_texts = []
            self._graph.reset()
            self._draft_path = None

            live_state.set_live_active()
            worker.start()
        if source == SYSTEM_SOURCE:
            _emit_tauri_event("start", session_id)
        self._broadcast(self.status())

    def feed_pcm(self, data: bytes) -> None:
        """Consume one binary WS frame of 16 kHz/mono/Int16 PCM."""
        if len(data) % 2:  # defend against truncated frames (Int16 = 2 bytes)
            data = data[:-1]
        with self._lock:
            if self._state != "recording" or not data:
                return
            assert self._wav and self._segmenter and self._worker
            self._wav.writeframes(data)
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            samples /= _INT16_SCALE
            self._samples_received += samples.size
            events = self._segmenter.process(samples)
            worker = self._worker
        for event in events:
            self._dispatch_event(worker, event)

    def stop(self) -> None:
        """Finalize: flush VAD, drain worker, hand the WAV to the batch input/."""
        with self._lock:
            if self._state != "recording":
                raise LiveSessionError("no live session is recording")
            self._state = "finalizing"
            segmenter = self._segmenter
            worker = self._worker
            wav = self._wav
            wav_path = self._wav_path
            session_id = self._session_id
            source = self._source
        # Announce the stop as soon as the state flips to "finalizing" so the
        # Rust shell halts native capture immediately (a finalize can take up
        # to a minute; feeding PCM into it would be wasted work).
        if source == SYSTEM_SOURCE and session_id:
            _emit_tauri_event("stop", session_id)
        self._broadcast(self.status())

        try:
            assert segmenter and worker and wav and wav_path and session_id
            for event in segmenter.flush():
                self._dispatch_event(worker, event)
            worker.stop(wait_seconds=60.0)
            wav.close()

            # Graceful finalize: atomically drop the .part suffix first, so a
            # crash between here and the move leaves a well-formed WAV, and
            # only in-progress recordings ever carry .part (recovery marker).
            final_wav = wav_path.with_suffix("")  # live_<id>.wav.part -> .wav
            os.replace(wav_path, final_wav)

            target = self._unique_target(session_id)
            self._source_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(final_wav), str(target))

            draft = self._draft_path
            self._broadcast(
                {
                    "type": "finalized",
                    "wav": str(target),
                    "draft": str(draft) if draft else None,
                }
            )
        except Exception as error:
            self._broadcast({"type": "error", "message": f"finalize failed: {error}"})
        finally:
            with self._lock:
                self._state = "idle"
                self._wav = None
                self._wav_path = None
                self._segmenter = None
                self._worker = None
                self._session_id = None
                self._source = None
                live_state.clear_live_active()
            self._broadcast(self.status())

    # --- listeners / reconnect recovery ------------------------------------

    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def replay(self, send: Listener) -> None:
        """Send current status + final history to a (re)connected client."""
        with self._lock:
            messages = [self.status(), *self._final_history]
            if self._final_texts:
                messages.append(self._keywords_message_locked())
            graph_snapshot = self._graph.snapshot()
            if graph_snapshot["nodes"]:
                messages.append(graph_snapshot)
        for message in messages:
            send(message)

    def client_connected(self) -> None:
        with self._lock:
            self._clients += 1
            if self._disconnect_timer is not None:
                self._disconnect_timer.cancel()
                self._disconnect_timer = None

    def client_disconnected(self) -> None:
        """Arm the auto-finalize timer when the last client drops mid-recording."""
        with self._lock:
            self._clients = max(0, self._clients - 1)
            if self._clients > 0 or self._state != "recording":
                return
            timer = threading.Timer(
                self._disconnect_finalize_seconds, self._auto_finalize
            )
            timer.daemon = True
            self._disconnect_timer = timer
            timer.start()

    # --- internals ----------------------------------------------------------

    def _auto_finalize(self) -> None:
        with self._lock:
            self._disconnect_timer = None
            if self._clients > 0 or self._state != "recording":
                return
        try:
            self.stop()
        except LiveSessionError:
            pass

    def _make_emit(self, session_id: str) -> Callable[[dict[str, Any]], None]:
        """Bind worker output to its session: drop messages emitted after the
        session ended (e.g. an inference that outlived the stop() drain)."""

        def emit(message: dict[str, Any]) -> None:
            with self._lock:
                if self._session_id != session_id:
                    return
            self._on_worker_message(message)

        return emit

    def _unique_session_id(self, started: datetime) -> str:
        """Minute-granularity id, uniquified against every artifact location
        (draft in output/, WAV in input/ and done/, temp WAV) so two sessions
        within the same minute never share files."""
        base = started.strftime("%Y%m%d_%H%M")
        candidate = base
        counter = 2
        while (
            (self._output_dir / f"meeting_{candidate}_live_draft.txt").exists()
            or (self._source_dir / f"meeting_{candidate}.wav").exists()
            or (self._done_dir / f"meeting_{candidate}.wav").exists()
            or (self._temp_dir / f"live_{candidate}.wav").exists()
            or (self._temp_dir / f"live_{candidate}.wav{PART_SUFFIX}").exists()
        ):
            candidate = f"{base}_{counter}"
            counter += 1
        return candidate

    def _dispatch_event(self, worker: TranscriptionWorker, event) -> None:
        if event.kind in ("start", "update"):
            worker.on_utterance_update(event.utterance_id, event.audio, event.t0)
        elif event.kind == "end":
            worker.on_utterance_end(event.utterance_id, event.audio, event.t0, event.t1)
        elif event.kind == "cancel":
            worker.on_utterance_cancel(event.utterance_id, event.t0)

    def _on_worker_message(self, message: dict[str, Any]) -> None:
        """Runs on the transcription worker thread."""
        extra: list[dict[str, Any]] = []
        if message.get("type") == "final" and message.get("text"):
            with self._lock:
                self._final_history.append(message)
                self._final_texts.append(message["text"])
                self._append_draft_locked(message)
                extra.append(self._keywords_message_locked())
                extra.append(self._graph_message_locked(message["text"]))
        self._broadcast(message)
        for item in extra:
            self._broadcast(item)

    def _keywords_message_locked(self) -> dict[str, Any]:
        terms = extract_terms(
            "\n".join(self._final_texts), limit=config.LIVE_KEYWORD_LIMIT
        )
        return {
            "type": "keywords",
            "items": [{"word": t.word, "score": t.score} for t in terms],
        }

    def _graph_message_locked(self, text: str) -> dict[str, Any]:
        """Per-final term candidates -> full graph snapshot broadcast."""
        terms = extract_terms(text, limit=config.LIVE_GRAPH_CANDIDATES_PER_FINAL)
        self._graph.add_utterance(terms)
        return self._graph.snapshot()

    def _append_draft_locked(self, message: dict[str, Any]) -> None:
        if self._draft_path is None:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            self._draft_path = (
                self._output_dir / f"meeting_{self._session_id}_live_draft.txt"
            )
        t0 = float(message.get("t0") or 0.0)
        stamp = f"{int(t0 // 60):02d}:{int(t0 % 60):02d}"
        with self._draft_path.open("a", encoding="utf-8") as draft:
            draft.write(f"[{stamp}] {message['text']}\n")

    def _unique_target(self, session_id: str) -> Path:
        target = self._source_dir / f"meeting_{session_id}.wav"
        counter = 2
        while target.exists():
            target = self._source_dir / f"meeting_{session_id}_{counter}.wav"
            counter += 1
        return target

    def _broadcast(self, message: dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(message)
            except Exception:
                pass  # a dead listener must not break the others


def _default_engine_provider():
    from .engine import get_engine

    return get_engine()


manager = LiveSessionManager()
