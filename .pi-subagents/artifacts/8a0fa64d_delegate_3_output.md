# Simplify Review — Tauri Desktop App Implementation

**Scope:** Static review of the Rust shell, Python backend, and packaging/CI files
for over-engineering, unnecessary abstractions, dead code, and simplification
opportunities. No builds or tests were run.

**Headline:** The codebase is generally well-structured and the complexity in
the Rust shell (Job Objects, WASAPI, WebView2 COM) is largely justified by the
Windows platform requirements. There is one **critical** compile error
(missing struct fields), a few **major** items (unused Cargo features,
over-defensive fallbacks), and a handful of **minor** cleanups. The overall
engineering quality is high; this is not an over-abstracted codebase.

---

## Findings

### [critical] main.rs — `AppState` initialization missing two fields (will not compile)

`app.manage(AppState { ... })` in `main()` initializes only `paths`, `backend`,
`port`, `allowed_origin`, `capture_tx`, `download_running`, and
`backend_starting`. The struct definition declares **ten** fields —
`feeder_token` and `shutdown_requested` are missing from the initializer.

This is a hard compile error. `feeder_token` is read in `start_backend_inner`
(and passed to `capture::spawn_controller`), and `shutdown_requested` is
read/written in `start_backend_inner` and `on_backend_exit`.

**File:** `src-tauri/src/main.rs` — `main()` function, `app.manage(AppState { ... })` block.

**Fix:** Add the two missing fields:
```rust
feeder_token: Arc::new(Mutex::new(String::new())),
shutdown_requested: AtomicBool::new(false),
```

---

### [major] Cargo.toml — `tokio` features `fs` and `io-util` appear unused

`tokio` is declared with features `["rt-multi-thread", "macros", "time",
"sync", "net", "fs", "io-util"]`. Reviewing all source files:

- **`fs`**: No direct `tokio::fs::*` call exists. The downloader (`downloader.rs`)
  uses `std::fs` exclusively, even inside `spawn_blocking` closures. `reqwest`
  and `tokio-tungstenite` enable their own tokio features transitively.
- **`io-util`**: No direct `tokio::io` utility usage (no `AsyncBufReadExt`,
  `AsyncReadExt`, etc.).

These two features pull in additional compilation units for no direct benefit.
They may be required transitively by `reqwest` or `tokio-tungstenite`, but
those crates enable what they need themselves — explicitly listing them here
is redundant at best.

**File:** `src-tauri/Cargo.toml` — `tokio` dependency line.

**Suggestion:** Drop `"fs"` and `"io-util"` and verify the crate still builds
(unlikely to break since transitive deps re-enable them if needed).

---

### [major] downloader.rs — `resolve_python_exe` has 6 fallback paths that are never exercised

`resolve_python_exe` checks a manifest-provided `python_exe` first (correct),
then falls back through six hardcoded candidate paths:
`python.exe`, `runtime/python.exe`, `python/python.exe`, `install/python.exe`,
`runtime/install/python.exe`, `python/install/python.exe`.

However, `packaging/backend/build.ps1` **always** emits `"python_exe":
"runtime/python.exe"` in `backend-manifest.json` (the zip layout puts
everything under a top-level `runtime/` folder). The manifest field is
declared `Option<String>` but is always present in practice.

The six-way fallback adds ~15 lines of code and a non-trivial error message
for a situation that cannot occur with the current build pipeline. If the
manifest is missing `python_exe`, that is a build-pipeline bug that should
fail loudly, not silently probe six layouts.

**File:** `src-tauri/src/downloader.rs` — `resolve_python_exe` function.

**Suggestion:** Reduce to the manifest value plus one fallback (`runtime/python.exe`
to match the known build.ps1 layout). If neither matches, fail. Alternatively,
make `python_exe` required in the manifest (`String` not `Option<String>`) and
drop all fallbacks.

---

### [minor] process.rs — `#[allow(clippy::too_many_arguments)]` on a 4-argument function

`contract_env` carries `#[allow(clippy::too_many_arguments)]` but takes only
4 parameters (`base_dir`, `model_cache_dir`, `ffmpeg_exe`, `hf_token`).
The clippy lint triggers at 7+ arguments. This allow is dead — it suppresses
a lint that cannot fire.

**File:** `src-tauri/src/process.rs` — `contract_env` function.

**Fix:** Remove the `#[allow(clippy::too_many_arguments)]` attribute.

---

### [minor] main.rs — `RunEvent::Exit` handler is redundant

`app.run()` handles both `RunEvent::ExitRequested` and `RunEvent::Exit` by
calling `shutdown_backend`. The comment says "belt and braces," but:

- `shutdown_backend` is already idempotent (takes the `Mutex<Option>`, stores
  nothing if already taken).
- `ExitRequested` always fires before `Exit` in normal shutdown.
- The Job Object's `KILL_ON_JOB_CLOSE` covers abnormal termination regardless.

The `RunEvent::Exit` arm adds no value. It is not harmful, but it is dead
logic that implies a gap that does not exist.

**File:** `src-tauri/src/main.rs` — `app.run()` closure.

**Suggestion:** Remove the `RunEvent::Exit` arm (keep `ExitRequested` only).

---

### [minor] main.rs — `on_backend_exit` emits ad-hoc JSON, breaking the `BackendStatusEvent` shape

`emit_backend_status` serializes `BackendStatusEvent { stage, message }`.
But `on_backend_exit` emits a raw `serde_json::json!` object with `stage`,
`state`, `code`, and `message` — a different shape from every other
`backend-status` event. The comment says this is intentional ("keeps the
bootstrap page's existing listener working"), but it means the frontend
receives two incompatible schemas on the same event channel.

**File:** `src-tauri/src/main.rs` — `on_backend_exit` function.

**Suggestion:** Either extend `BackendStatusEvent` with optional `state` and
`code` fields so all `backend-status` events share one type, or document the
two shapes explicitly in a shared contract. The current ad-hoc JSON is a
maintenance trap.

---

### [minor] capture.rs — `dropped` frame counter is write-only until exit

`ws_feeder` maintains an `AtomicU64` `dropped` counter that accumulates
`RecvError::Lagged` counts. It is only read once, at function exit, and only
printed to stderr if non-zero. During a long-lived capture session this
counter provides no observable signal — no metric, no event, no log line
until the feeder task ends.

**File:** `src-tauri/src/capture.rs` — `ws_feeder` function.

**Suggestion:** Either emit a periodic log line (e.g. every N dropped frames)
or remove the counter entirely. As-is it is a small amount of complexity with
near-zero operational value.

---

### [minor] capture.rs — `mono_scratch` take/put-back dance

In `Pipeline::push_f32`, `mono_scratch` is filled, then `std::mem::take`'n
out, passed to `self.resampler.process(&mono, ...)`, then put back via
`self.mono_scratch = mono`. This is done to avoid reallocating the scratch
buffer, but it is convoluted. `process` takes `&[f32]` (immutable borrow),
so the take/put-back is unnecessary — a direct `self.resampler.process(&self.mono_scratch, ...)`
would work since `process` does not mutate the input slice.

**File:** `src-tauri/src/capture.rs` — `Pipeline::push_f32` method.

**Suggestion:** Replace the take/process/put-back with a direct borrow:
```rust
self.resampler.process(&self.mono_scratch, &mut self.pending);
```
Verify that `LinearResampler::process` does not alias `mono_scratch` with
`self.pending` (it does not — `pending` is a separate field).

---

### [minor] app.py — deprecated `/jobs/{filename}/transcript` route

The route is marked "Deprecated: kept for one release for compatibility (D2).
The UI now links to the full detail page below instead." If the compatibility
window has elapsed, this is dead code that adds a route, a template dependency
(`_transcript.html`), and a traversal-check path.

**File:** `src/web/app.py` — `transcript` route handler.

**Suggestion:** Verify whether the deprecation period has expired. If so,
remove the route and the `_transcript.html` template. If still needed, add a
sunset date to the comment.

---

### [minor] webview.rs — `let _ = app;` to suppress unused-variable warning

`navigation_allowed` takes `app: &AppHandle` but only uses it in the non-http
`else` branch (`let _ = app;`). The parameter exists to open external URLs
via `tauri_plugin_opener`, but that path does not use `app` — it calls
`tauri_plugin_opener::open_url` directly. The `app` parameter is genuinely
unused.

**File:** `src-tauri/src/webview.rs` — `navigation_allowed` function.

**Suggestion:** Remove the `app` parameter from `navigation_allowed` (and
update the closure in `create_main_window` accordingly). The `let _ = app;`
is a sign the parameter should not be there.

---

### [info] downloader.rs — double zip-slip protection is justified, not over-engineered

`extract_zip` uses both `enclosed_name()` and a canonicalize-and-check
approach. This is correctly described as "belt and braces" for a security-
critical path that extracts a downloaded archive. **No change recommended.**

---

### [info] Entire Rust shell — uncompiled (【未検証】 markers)

Every Rust module carries 【未検証】 (unverified) markers stating the code was
authored on WSL2 Linux and never compiled against the Windows toolchain.
This means type-level issues (like the critical missing-fields bug above)
are likely more widespread — windows-rs API signatures, webview2-com type
paths, and `WebviewWindow::navigate` mutability are all flagged as
written-from-memory. The critical finding above is likely the first of
several compile errors that will surface on the first real build.

**Not a code change** — but the review cannot certify correctness of any
unsafe block, COM interop path, or cpal/WASAPI assumption without a Windows
build.

---

### [info] build.ps1 / fetch.ps1 / desktop-windows.yml — no simplification needed

The packaging scripts are thorough but not over-engineered:
- `build.ps1`: CUDA guard, relocation smoke test, and deterministic zip are
  all justified by the CPU-only contract and reproducibility requirements.
- `fetch.ps1`: License checks (LGPL verification, forbidden flag scan) are
  required for legal compliance, not gold-plating.
- `desktop-windows.yml`: The 4-job pipeline (tests → backend → tauri → e2e)
  is well-structured. The e2e-smoke `continue-on-error` is appropriate for an
  experimental gate. The signing TODO is documented, not silently omitted.

---

## Summary Table

| # | Severity | File | Issue |
|---|----------|------|-------|
| 1 | critical | main.rs | `AppState` init missing `feeder_token` + `shutdown_requested` — won't compile |
| 2 | major | Cargo.toml | `tokio` `fs`/`io-util` features likely unused |
| 3 | major | downloader.rs | 6-way `python_exe` fallback never exercised |
| 4 | minor | process.rs | Dead `#[allow(clippy::too_many_arguments)]` |
| 5 | minor | main.rs | Redundant `RunEvent::Exit` handler |
| 6 | minor | main.rs | `on_backend_exit` ad-hoc JSON breaks event schema |
| 7 | minor | capture.rs | `dropped` counter write-only until exit |
| 8 | minor | capture.rs | `mono_scratch` take/put-back unnecessary |
| 9 | minor | app.py | Deprecated `/transcript` route — verify sunset |
| 10 | minor | webview.rs | Unused `app` param + `let _ = app;` |
| 11 | info | downloader.rs | Double zip-slip protection — justified, no change |
| 12 | info | all Rust | Uncompiled; 【未検証】 — type issues likely broader |
| 13 | info | packaging/CI | No simplification needed |