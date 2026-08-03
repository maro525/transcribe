# Logic Review — Tauri Desktop App Implementation

**Scope:** Static review of Python backend + Rust shell + cross-check against
test suite. No builds or tests were run.

## Summary

| Severity | Count | Highlights |
|----------|-------|------------|
| critical | 1 | `recovery.py` second loop directly contradicts `test_finished_wavs_in_temp_are_left_alone` — test will fail. |
| major    | 2 | `test_live_ws_feeder.py` control-client tests are broken (TestClient sends no Origin → control messages silently ignored); `PYTHONHOME=""` env override can break the bundled Python's prefix resolution. |
| minor    | 5 | Shutdown-timing margin is only 5 s; `shutdown_requested` flag is dead code; downloader `python_exe` traversal; resampler aliasing; `chunks_exact` frame loss. |
| info     | 6 | Feeder client-counting design is sound; broadcast overflow semantics correct; various defensive checks noted. |

The single most important contradiction is between `recovery.py` and
`test_wav_recovery.py::test_finished_wavs_in_temp_are_left_alone`. This is a
**real, reproducible test failure**, not a theoretical risk.

---

## Findings

### [critical] recovery.py second loop moves `live_*.wav` that the test expects to be left alone

**Files:** `src/live/recovery.py:69-74`, `tests/test_wav_recovery.py:test_finished_wavs_in_temp_are_left_alone`

`recover_orphaned_wavs` has two loops. The second loop:

```python
for orphan in sorted(temp_dir.glob("live_*.wav")):
    try:
        recovered.append(_move_recovered(orphan, source_dir, orphan.name))
    except Exception as error:
        print(f"Live recovery failed for {orphan.name}: {error}")
```

The glob pattern `"live_*.wav"` matches **any** file named `live_*.wav` in the
temp dir — including `live_done.wav` created by the test:

```python
keeper = temp_dir / "live_done.wav"  # no .part suffix — not orphaned
keeper.write_bytes(_wav_header() + b"\x00\x00" * SR * 2)
assert recover_orphaned_wavs(temp_dir, source_dir) == []
assert keeper.exists()
```

The second loop will call `_move_recovered(keeper, source_dir, "live_done.wav")`,
which does `shutil.move(keeper, source_dir/"recovered_live_done.wav")`. Result:

- `recovered` = `[source_dir/"recovered_live_done.wav"]`, **not** `[]` → first assert fails.
- `keeper` no longer exists at its original path → second assert fails.

**This is a real contradiction.** The test will fail against the current
implementation.

**Which is correct?** The `recovery.py` module docstring states the second
loop's purpose: recover "finalized-but-stranded" WAVs left by a crash between
the `.part → .wav` rename and the move to `input/`. In real operation the temp
dir is private to the live session, so any `live_*.wav` found at startup *is*
stranded and should be moved — the implementation's behavior is defensible.
The test's mental model ("no `.part` suffix → not orphaned → leave alone") was
likely written before the second loop was added and is stale.

**Resolution:** either update the test to reflect the second-loop recovery
semantics (the stranded-finalized feature is intentional), or narrow the
second loop's glob so it does not sweep up files the test considers finished.
Given the docstring's stated intent, updating the test is the more consistent
fix — but the current state is a guaranteed CI failure either way.

---

### [major] `test_live_ws_feeder.py` control-client tests cannot pass — TestClient sends no Origin header

**Files:** `src/web/app.py:live_ws`, `tests/test_live_ws_feeder.py`

`live_ws` classifies a connection as a "feeder" (PCM-only, control messages
ignored, not counted as a client) when there is no `Origin` header:

```python
is_feeder = not websocket.headers.get("origin")
...
elif message.get("text") and not is_feeder:
    await _handle_live_control(message["text"], listener)
```

Starlette's `TestClient.websocket_connect` does **not** send an `Origin`
header by default (confirmed by `test_same_origin_browser_is_accepted`
explicitly passing `headers={"Origin": "http://testserver"}`). The feeder test
acknowledges this in its comment ("TestClient sends no Origin header by
default") but then uses a **control** client without an Origin header:

```python
with client.websocket_connect("/live/ws") as control:
    assert control.receive_json()["type"] == "status"
    control.send_json({"type": "start", "source": "system"})
    _wait(lambda: manager.status()["state"] == "recording")  # <-- times out
```

Because `is_feeder=True`, the `{"type": "start"}` text frame is silently
dropped (the `not is_feeder` guard skips `_handle_live_control`). The session
never enters "recording". `_wait(...)` times out after 5 s → `AssertionError`.

**Affected tests:**
- `test_feeder_without_origin_header_is_accepted_and_feeds_same_session` —
  fails at the first `_wait` (state never becomes "recording").
- `test_feeder_disconnect_does_not_kill_session_while_control_connected` —
  same root cause: control's `send_json({"type": "start"})` is ignored.

`test_cross_origin_browser_is_still_rejected` and
`test_same_origin_browser_is_accepted` explicitly set `Origin` and are fine.

**Fix:** the control client must pass `headers={"Origin": "http://testserver"}`
so it is treated as a browser (non-feeder) and its control messages are
honored. The feeder client should remain Origin-less.

---

### [major] `PYTHONHOME=""` env override may break the bundled Python's prefix resolution

**File:** `src-tauri/src/process.rs:contract_env` / `build_env_block`

The contract env always sets:

```rust
("PYTHONPATH".into(), "".into()),
("PYTHONHOME".into(), "".into()),
("PYTHONSTARTUP".into(), "".into()),
```

The comment claims "CPython treats empty PYTHONHOME/PYTHONPATH/PYTHONSTARTUP
as unset." This is **not reliably true for `PYTHONHOME`**:

- `PYTHONPATH=""` — safe. CPython splits on `os.pathsep` and skips empty
  entries; an empty PYTHONPATH yields no additional import paths.
- `PYTHONSTARTUP=""` — safe. No startup file is loaded.
- `PYTHONHOME=""` — **risky.** CPython's path config reads `PYTHONHOME` to set
  `sys.prefix` / `sys.exec_prefix`. When the variable is *set* (even to `""`),
  some CPython versions use it as the home directory instead of falling back
  to the executable-path calculation. An empty home can yield
  `sys.prefix = ""` and break `import` / stdlib discovery. The behavior is
  version- and build-dependent; python-build-standalone may or may not
  override this via its embedded `python3._pth`.

Since `build_env_block` always includes `PYTHONHOME=""` (even when the parent
process does not have `PYTHONHOME` set), the child **always** receives an
empty `PYTHONHOME`. If the bundled runtime does not tolerate this, the backend
will fail to start — a hard launcher failure on first run.

**Recommendation:** only inject `PYTHONHOME=""` when the parent process
actually has `PYTHONHOME` set (to neutralize a leaked Anaconda/system home).
When the parent does not have it, omit it entirely so the bundled Python uses
its embedded path configuration. The same applies (less critically) to
`PYTHONPATH` and `PYTHONSTARTUP`. Given the `【未検証】` markers throughout the
crate, this must be validated on a real Windows build.

---

### [minor] Shutdown timing: 5-second margin between Python graceful exit and Rust hard kill

**Files:** `src/web/app.py:_graceful_shutdown`, `src-tauri/src/process.rs:shutdown_blocking`

| Side | Budget |
|------|--------|
| Python finalize wait (`FINALIZE_WAIT_SECONDS`) | 65 s |
| uvicorn `timeout_graceful_shutdown` | 5 s |
| **Python total worst case** | **70 s** |
| Rust `SHUTDOWN_WAIT_MS` | 75 s |
| **Margin** | **5 s** |

The ordering is **correct** (70 < 75), so under normal conditions Rust does
not kill a still-finalizing backend. However:

1. If the live-session finalize (auto-finalize path) takes the full 65 s poll
   window and the worker drain took close to 60 s, the 65 s deadline can
   expire while `state` is still `"finalizing"`. `_graceful_shutdown` then
   sets `should_exit=True` and uvicorn force-closes after 5 s — the daemon
   thread running `stop()` is abandoned on process exit, so the WAV may never
   be moved to `input/` (data loss). The process still exits within 75 s, so
   Rust's kill does not fire, but the recording is lost.

2. The 5 s margin is thin. Any additional latency (e.g., uvicorn connection
   cleanup slower than expected, GC pauses) could push Python past 75 s,
   causing a `TerminateProcess` that abandons the finalize mid-flight.

This is acceptable for v1 but should be widened (e.g., Rust 90 s, or Python
finalize wait 55 s) to give the finalize reliable headroom.

---

### [minor] `shutdown_requested` flag is dead code — never set to `true`

**File:** `src-tauri/src/main.rs`

`AppState.shutdown_requested` is:
- initialized `AtomicBool::new(false)` (main.rs:128),
- stored `false` in `start_backend_inner` (main.rs:336),
- read in `on_backend_exit` (main.rs:381) to distinguish requested vs.
  unexpected shutdown.

It is **never set to `true`**. `shutdown_backend` (the normal exit path) does
not set it before calling `process::shutdown_blocking`. The design works only
because `shutdown_backend` takes the `BackendHandle` out of the `Mutex`
*before* the process exits, so `on_backend_exit`'s `g.take()` returns `None`
and it bails out early ("already cleared"). The `shutdown_requested` check is
therefore unreachable dead code. Functionally safe today, but misleading: a
future refactor that changes the take-from-mutex sequence could silently break
the requested/unexpected distinction. Either set the flag in
`shutdown_backend` or remove it and document the mutex-take guard as the sole
mechanism.

---

### [minor] `resolve_python_exe` does not validate the manifest `python_exe` path against traversal

**File:** `src-tauri/src/downloader.rs:resolve_python_exe`

```rust
if let Some(rel) = manifest_rel {
    candidates.push(rel.to_string());
}
...
for rel in &candidates {
    if dir.join(rel).is_file() {
        return Ok(rel.clone());
    }
}
```

`manifest_rel` (from the bundled `backend-manifest.json`'s `python_exe` field)
is joined directly. If the manifest is tampered to contain `"../../python.exe"`,
`dir.join(rel)` escapes the extracted directory and the returned `python_rel`
is stored in `current-runtime.json`. On the next launch, `safe_runtime_record`
rejects `..` components, so `installed_runtime` returns `None` — the user is
stuck (installed but unusable, and re-install hits the idempotent check). The
manifest is bundled (not user-supplied), so risk is low, but `resolve_python_exe`
should reject non-normal-component paths for defense in depth, mirroring
`safe_runtime_record`.

---

### [minor] Linear resampler has no anti-aliasing filter (acknowledged)

**File:** `src-tauri/src/capture.rs:LinearResampler`

48 kHz → 16 kHz downsampling via linear interpolation with no low-pass filter.
Content above 8 kHz at the source rate aliases into the 0–8 kHz band, adding
artifacts to the ASR input. The module docs explicitly acknowledge this as an
accepted v1 limitation ("a windowed-sinc / polyphase resampler is a drop-in
upgrade if ASR quality measurably suffers"). No code defect; flagged so it is
a conscious decision, not an oversight. Worth re-evaluating if word-error-rate
regresses on system-audio capture vs. mic capture.

---

### [minor] `chunks_exact` drops partial trailing audio frames at channel boundaries

**File:** `src-tauri/src/capture.rs:Pipeline::push_f32`

```rust
for frame in interleaved.chunks_exact(self.channels) {
    self.mono_scratch.push(frame.iter().sum::<f32>() * inv);
}
```

`chunks_exact` silently discards any remainder shorter than `channels` samples
at the end of a callback block. cpal callbacks should always deliver whole
frames, so in practice this is zero loss. But if a driver ever delivers a
non-integral frame count, a few samples are silently dropped per block.
Negligible for speech; noted for completeness.

---

### [info] Feeder WS client-counting design is correct and consistent

**Files:** `src/web/app.py:live_ws`, `src/live/session.py:client_connected/disconnected`

Feeders (`is_feeder=True`, no Origin) do **not** call `client_connected` /
`client_disconnected`, so they are excluded from the `_clients` count. The
60 s auto-finalize arms only when the last *browser* client disconnects
mid-recording. This matches `test_feeder_disconnect_does_not_kill_session_while_control_connected`'s
intent (though that test is broken for the unrelated Origin-header reason
above). A feeder connecting does not cancel the disconnect timer (correct —
the browser is the user; a feeder should not keep the session alive alone).
The `max(0, self._clients - 1)` underflow guard is sound.

---

### [info] Broadcast channel overflow semantics are correct

**File:** `src-tauri/src/capture.rs`

`tokio::sync::broadcast::channel(FRAME_QUEUE=64)` is a bounded ring buffer.
`broadcast::Sender::send` is synchronous and never blocks; on overflow the
**oldest** buffered frame is dropped to make room, and the receiver observes
`RecvError::Lagged(n)`. This matches the comments exactly: the audio callback
never blocks, and after a WS reconnect the feeder sends fresh audio (the
receiver catches up to the current tail), not stale audio. The `Lagged`
counter is accumulated and logged at feeder exit. `RecvError::Closed` (sender
dropped on capture stop) cleanly terminates the feeder via `break 'outer`.
FRAME_QUEUE=64 × 2048 samples / 16 kHz ≈ 8.2 s of buffering, consistent with
the "~8 s" comment.

---

### [info] Late worker emit after `stop()` has a small but bounded race

**File:** `src/live/session.py:_make_emit`

`emit` checks `self._session_id == session_id` under the lock, releases it,
then calls `_on_worker_message` (which re-acquires the lock). A concurrent
`stop()` could flip `_session_id` to `None` in the window between the check
and `_on_worker_message`, causing a late final to be appended to
`_final_history` / the draft file after the session is technically idle.
`worker.stop(wait_seconds=60)` drains the worker first, so in practice no
emits occur after the drain completes; the race is theoretical. Not a defect
for v1, but a `_session_id` re-check inside `_on_worker_message` would close
it completely.

---

### [info] Dynamic-port startup: healthz may be delayed by `prepare()` but within timeout

**File:** `src/main.py:_serve_dynamic_port`

In Tauri mode, `prepare=_startup_tasks` runs *after* `TAURI_READY` is printed
but *before* `asyncio.run(server.serve(sockets=[sock]))`. The socket is bound
(connections queue in the TCP backlog), but uvicorn does not accept until
`serve` starts. The Rust shell's `wait_healthz` polls with a 2 s per-request
timeout and 60 s overall deadline. If `prepare()` (recovery scan,
`bootstrap_history`) takes >2 s, early healthz requests time out but are
retried; once uvicorn starts, the next poll succeeds. Safe within the 60 s
deadline for typical startup. Noted because a slow `bootstrap_history` (large
`input/` directory) could consume most of the healthz budget.

---

### [info] `post_shutdown` HTTP is hand-rolled but correct

**File:** `src-tauri/src/process.rs:post_shutdown`

Uses a raw `TcpStream` with 2 s connect/read/write timeouts, sends a minimal
HTTP/1.1 POST with `X-Shutdown-Token`, and drains the 202 response. The
`format!("127.0.0.1:{port}").parse().unwrap()` is safe (the format always
produces a valid `SocketAddr`). Runs synchronously from `RunEvent::ExitRequested`
without touching the async runtime, as intended.

---

## Residual Risks

1. **[critical] CI will fail** on `test_finished_wavs_in_temp_are_left_alone`
   until the test/implementation contradiction is resolved.
2. **[major] CI will fail** on two `test_live_ws_feeder.py` tests until the
   control client sets an `Origin` header.
3. **[major] First-launch backend startup** may fail if the bundled
   python-build-standalone runtime does not tolerate `PYTHONHOME=""`. Must be
   verified on a real Windows build; the entire Rust shell is marked `【未検証】`.
4. **[minor] Recording data loss** is possible if a live finalize exceeds the
   65 s budget during shutdown (daemon thread abandoned, WAV not moved to
   `input/`).
5. **[minor] Resampler aliasing** may degrade ASR quality on system-audio
   capture; needs WER comparison before declaring v1 done.
6. The Rust shell has never been compiled or executed (WSL2 dev host); all
   windows-rs / webview2-com / cpal signatures are written from memory and
   must be validated on first Windows build.