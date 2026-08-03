# Quality Review — feature/tauri-desktop (Tauri desktop app)

**Scope:** Static review of 9 Python files (Phase A) and 6 Rust files + 2 config files (Phase B).
**Method:** Read-only review. No builds, tests, or type-checks executed.
**Dimensions:** naming consistency, code duplication, function length, SOLID adherence, documentation quality, error message clarity.

## Summary

The codebase is **well-documented and thoughtfully designed**: contracts are
explicit, security boundaries (DNS-rebinding, CSWSH, zip-slip, path traversal,
license gating) are deliberate and commented, and the frozen wire protocol
between the Python backend and Rust shell is clearly specified. Documentation
quality is a clear strength — nearly every non-obvious decision has a
rationale comment.

However, the Rust shell **has never been compiled** (acknowledged via 【未検証】
markers), and this shows: there are at least **two definite compile errors**
in `main.rs` (missing struct fields in the `AppState` initializer and a
wrong-arity call to `capture::spawn_controller`). These are **critical** and
block the crate from building as written. Beyond that, `shutdown_requested`
is declared and read but **never set to true**, making the exit-monitor's
"requested vs unexpected" distinction dead logic. Several Rust functions are
long and mix concerns (SRP), and there is minor cross-language duplication of
the protocol-version constant.

The Python backend is in materially better shape: no correctness bugs found,
consistent naming, good module decomposition, and clear error messages. The
findings there are all minor/style-level.

**Severity counts:** 2 critical · 2 major · 13 minor · 5 info

---

## Findings

### Critical

- **[critical] `src-tauri/src/main.rs` — `AppState` initializer omits two fields.**
  The struct declares `feeder_token: Arc<Mutex<String>>` and
  `shutdown_requested: AtomicBool`, but the `app.manage(AppState { ... })`
  block in `main()` initializes only `paths, backend, port, allowed_origin,
  capture_tx, download_running, backend_starting`. This is a definite compile
  error (`missing field`), and it also means the feeder-token contract
  (`X-Feeder-Token`) wiring is incomplete — `start_backend_inner` later does
  `state.feeder_token.lock()`, which would have no value even if it compiled.

- **[critical] `src-tauri/src/main.rs` — `capture::spawn_controller` called with wrong arity.**
  `main()` calls `capture::spawn_controller(port.clone())` (one argument), but
  `capture::spawn_controller(port: Arc<AtomicU16>, feeder_token: Arc<Mutex<String>>)`
  requires two. Definite compile error. The `feeder_token` value is never
  constructed in `main()` at all, so the capture controller cannot receive the
  shared secret it needs for the `/live/ws` feeder handshake.

### Major

- **[major] `src-tauri/src/main.rs` — `shutdown_requested` is never set to `true`.**
  `AppState.shutdown_requested` is stored to `false` in `start_backend_inner`
  and read in `on_backend_exit` to distinguish requested vs unexpected
  shutdown, but **no code path ever stores `true`** before
  `shutdown_backend()`/`process::shutdown_blocking()`. Consequently
  `on_backend_exit` always treats the exit as unexpected. It happens to be
  non-fatal because `shutdown_backend` already `take()`s the handle first, so
  the monitor's `take()` returns `None` and it exits early — but the flag is
  dead logic and the "backend died unexpectedly" UI event can still fire on a
  normal requested teardown in a race where the monitor runs before
  `shutdown_backend` takes the handle. `shutdown_backend` should set
  `shutdown_requested = true` before calling `shutdown_blocking`.

- **[major] `src-tauri/src/downloader.rs` — `download_with_resume` is ~120 lines and mixes four responsibilities.**
  It performs (1) partial-file re-hashing, (2) corrupt-partial reset, (3) the
  streaming download/append loop with Range/206 reconciliation, and (4)
  final SHA-256 verification. Each is a coherent unit; collapsing them into
  one function hurts readability and testability (SRP). Recommend extracting
  `hash_existing_partial`, the download loop, and `verify_digest`.

### Minor

- **[minor] `src-tauri/src/main.rs` — `start_backend_inner` is ~100 lines and mixes spawn / ready-wait / health-wait / navigate / monitor-spawn.**
  Each stage is sequential and well-commented, but the function is doing five
  distinct things. Extracting `wait_ready`, `gate_healthz`, and
  `install_exit_monitor` helpers would materially improve readability.

- **[minor] `src-tauri/src/main.rs` — inconsistent error-message language.**
  User-facing errors mix Japanese
  (`"ランタイムが未インストールです。先にダウンロードしてください。"`,
  `"起動処理は既に実行中です"`) and English
  (`"Credential Manager unavailable: {e}"`,
  `"could not resolve %LOCALAPPDATA%"`). If the bootstrap page is
  Japanese-only, the English strings will show untranslated. Pick one
  convention (or route through a message map).

- **[minor] `src-tauri/src/main.rs` — `on_backend_exit` emits an ad-hoc event shape.**
  It emits `serde_json::json!({ "stage", "state", "code", "message" })`,
  whereas every other `backend-status` emit uses the `BackendStatusEvent`
  struct (`stage`, `message` only). The comment explains the intent (extra
  `state`/`code` for death notifications), but the frontend now must handle
  two shapes for one event name. Consider a dedicated `backend-died` event or
  a typed struct.

- **[minor] `src-tauri/src/process.rs` — `spawn_backend` (Windows) is ~130 lines.**
  Inherently complex Win32 setup (pipes → CreateProcessW → Job Object →
  resume → drain threads), but the body could be split into `make_pipes`,
  `create_suspended_process`, `assign_job`, and `spawn_drains` helpers. The
  error-recovery closures (`kill_suspended`, `kill_suspended_with_job`) are a
  neat pattern but add to the cognitive load.

- **[minor] `src-tauri/src/capture.rs` — `ws_feeder` is ~80 lines with a nested `loop`/`select!`.**
  The reconnect-backoff outer loop and the inner send/poll loop could be
  separated (e.g. a `connect_and_stream` inner fn returning a "reconnect"
  signal). Currently control flow is hard to follow.

- **[minor] `src-tauri/src/capture.rs` — `Pipeline::push_f32` take/restore dance.**
  `let mono = std::mem::take(&mut self.mono_scratch); … self.resampler.process(&mono, …); self.mono_scratch = mono;`
  works around a borrow conflict but reads awkwardly. A comment explaining
  "borrowed immutably by resampler while we hold &mut self" would help, or
  refactor `LinearResampler::process` to take the slice without the
  self-borrow.

- **[minor] `src-tauri/src/capture.rs` — `run_audio_thread` is ~60 lines with a large `match sample_format` arm.**
  The three `build_input_stream` arms differ only in the f32 conversion
  closure; a small `to_f32` adapter per format would collapse them.

- **[minor] Cross-language duplication of the protocol-version constant.**
  `src/version.py` defines `PROTOCOL_VERSION = 1`; `src-tauri/src/process.rs`
  defines `EXPECTED_PROTOCOL: u64 = 1`; `downloader.rs` records
  `process::EXPECTED_PROTOCOL` into `current-runtime.json`. The integer `1`
  is duplicated across two languages with no shared generator. Drift is
  possible (and the comment in `process.rs` already warns "do not change
  without bumping protocol"). Consider emitting/validating the version in
  `backend-manifest.json` so the shell reads it from one source.

- **[minor] `src/web/app.py` + `main.py` — `DYNAMIC_PORT_ENV_VAR` defined in both modules.**
  Both files declare `DYNAMIC_PORT_ENV_VAR = "TRANSCRIBE_DYNAMIC_PORT"`
  independently. Import from a shared location (e.g. `config`) to avoid
  divergence.

- **[minor] `src/live/session.py` — magic string event kinds.**
  `_dispatch_event` compares `event.kind in ("start", "update", "end", "cancel")`
  and `SYSTEM_SOURCE = "system"` is matched in `start`/`stop`. These string
  literals are undocumented enums. An `Enum` (or module-level constants)
  would make the state machine self-documenting and prevent typos.

- **[minor] `src/live/session.py` — `assert` used for runtime invariants in `feed_pcm`/`stop`.**
  `assert self._wav and self._segmenter and self._worker` is stripped under
  `python -O`. Since these guard against a real (if shouldn't-happen) state
  violation, an explicit `if … is None: return` or `RuntimeError` is safer
  for shipped/deployment builds.

- **[minor] `src/live/session.py` — `_broadcast` silently swallows all listener exceptions.**
  `except Exception: pass` with only an inline comment. A dead/misbehaving
  listener is invisible to operators. At minimum a `print`/log would aid
  debugging; better, track and evict repeatedly-failing listeners.

- **[minor] `src/live/session.py` — `_unique_session_id` and `_unique_target` duplicate the "append counter until free" pattern.**
  Two near-identical while-loops with different candidate builders. A small
  `unique_path(base, suffix_fn)` helper would remove the duplication.

- **[minor] `src/audio.py` — `CalledProcessError` surfaces no stderr detail.**
  `subprocess.run(..., check=True, stderr=subprocess.PIPE)` captures stderr
  but the raised `CalledProcessError` message does not include it by default;
  callers get "Command returned non-zero exit status 1" with no ffmpeg
  output. Wrap and include `e.stderr.decode()` for actionable diagnostics.

### Info

- **[info] `src-tauri/Cargo.toml` / all Rust files — extensive 【未検証】 (unverified) markers.**
  Every Rust module and `Cargo.toml` explicitly states the crate was authored
  on WSL2 Linux and never compiled/run on Windows. This is honest and good
  practice, but it is the root cause of the two critical compile errors above
  and means the entire Rust shell is effectively unvalidated. The first
  Windows build will likely surface more issues (windows-rs 0.6x signatures,
  webview2-com type paths, cpal loopback behavior). This is the dominant
  residual risk.

- **[info] `src-tauri/tauri.conf.json` — CSP allows `'unsafe-inline'` for `script-src`.**
  Required for the inlined bootstrap scripts, but worth noting for a
  security-sensitive local app. Acceptable for v1; revisit if the bootstrap
  page grows.

- **[info] `src-tauri/tauri.conf.json` — updater uses `PLACEHOLDER.invalid` endpoint and placeholder pubkey.**
  `createUpdaterArtifacts` is `false`, so this is inert today, but it must be
  replaced before any release with auto-update.

- **[info] `src/live/recovery.py` — `_fmt_tag` unpacked but unused.**
  `_fmt_tag, channels, sample_rate = struct.unpack("<HHI", fmt[:8])` — the
  leading underscore correctly signals intentional discard; no action needed,
  noted only for completeness.

- **[info] `src/version.py` vs `downloader.rs` comments — version strings look adjacent but mean different things.**
  `BACKEND_VERSION = "2026.07.0"` (Python code version) vs manifest/runtime
  version `2026.07.1` (Python *runtime* package version) in downloader docs.
  Not a bug, but the proximity invites confusion; a one-line comment
  distinguishing "backend code version" from "runtime archive version" would
  help future readers.

---

## Residual Risks

1. **Rust shell has never compiled.** The two critical findings prove at least
   the `main.rs` wiring is broken; the 【未検証】 markers strongly suggest more
   issues (windows-rs signatures, webview2-com paths, cpal loopback) will
   surface only on a real Windows toolchain build.
2. **`shutdown_requested` flag is dead**, so the "unexpected death" UI path can
   misfire during a normal graceful shutdown under a thread race.
3. **`feeder_token` is never constructed** in `main()` — even after the compile
   errors are fixed, the `X-Feeder-Token` secret must actually be built and
   threaded into both `AppState.feeder_token` and `spawn_controller`, or the
   `/live/ws` feeder will be rejected by the Python backend when a secret is
   set.
4. **Protocol version duplicated across Python/Rust** with no shared source;
   a bump in one without the other fails at runtime with a clear error
   (good), but the drift risk remains.
5. **`download_with_resume` size/sanity logic is complex and untested**; the
   206/200 reconciliation and resume-rehash path is a likely source of
   subtle bugs on flaky networks and should be covered once a build exists.
6. **Python `assert`-based guards** are stripped under `-O`, weakening
   invariant protection in optimized deployments.