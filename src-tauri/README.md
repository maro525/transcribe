# src-tauri — Transcribe desktop shell (Tauri v2, Windows x64)

Rust shell that wraps the Python FastAPI backend as a Windows desktop app.
Everything in this directory was authored on a WSL2 Linux host **without a
Windows toolchain — the whole crate is 未検証 (never compiled, never run)**.
See "未検証 items" below before trusting anything here.

## Build (on Windows only)

Prerequisites:

- Rust (MSVC host): `rustup default stable-x86_64-pc-windows-msvc`
  (or `rustup target add x86_64-pc-windows-msvc`)
- Visual Studio Build Tools (C++), WebView2 runtime (dev machines usually have it)
- Tauri CLI: `cargo install tauri-cli --locked`
- Build-time inputs that other phases provide (referenced as bundle resources):
  - `../packaging/ffmpeg/ffmpeg.exe` (+ `LICENSE.txt`, `SOURCE.txt`) — BtbN LGPL static build (Phase C)
  - `../packaging/backend/manifest.json` — backend runtime manifest (Phase C), bundled as `backend-manifest.json`
  - `../main.py` and `../src/` — Python app code + templates (clean `__pycache__` before bundling; the resource glob copies the directory as-is)

Build:

```powershell
cd src-tauri
cargo tauri build --bundles nsis
```

Notes:

- `bundle.createUpdaterArtifacts` is `false` for the unsigned v1 (Gate 1
  decision): with `true`, the build would require `TAURI_SIGNING_PRIVATE_KEY`.
  When signing lands post-v1: generate a keypair with `tauri signer generate`,
  put the pubkey into `tauri.conf.json` (`plugins.updater.pubkey`, currently a
  placeholder) and flip this back to `true`.
- The updater endpoint in `tauri.conf.json` is a placeholder; the updater
  plugin is intentionally **not** registered in Rust yet.
- `Cargo.lock` is gitignored until the first real Windows build produces a
  verified lockfile; commit it then (bin crate convention) and remove the
  ignore entry.
- Version coupling: `windows` and `webview2-com` in `Cargo.toml` must match
  the versions the resolved `wry` uses (`cargo tree -p wry`). Adjust on first
  build.
- v1 is unsigned (no Authenticode): SmartScreen/Defender warnings are
  expected; user-facing bypass instructions belong in the top-level README.

## Runtime layout (created on first run)

```
%LOCALAPPDATA%/Transcribe/
  data/                # TRANSCRIBE_BASE_DIR (backend-owned)
    model_cache/       # HF_HOME / XDG_CACHE_HOME
  runtimes/
    cpu-<version>/     # python-build-standalone runtime (first-run download)
    download/          # .partial resume files
  logs/                # backend-stdout.log / backend-stderr.log (rotated)
  current-runtime.json
```

The Python runtime is **not** bundled (NSIS 2 GB cap). `downloader.rs` fetches
the zip from the GitHub Releases URL in the bundled `backend-manifest.json`
(`{version, url, sha256, size[, python_exe]}`) with Range-resume, SHA-256
verification, temp-dir extraction and atomic rename.

## Sidecar contract (frozen, protocol = 1)

- Spawn: `<runtime>/python.exe <resources>/main.py`, cwd = resource dir, env:
  `TRANSCRIBE_DYNAMIC_PORT=1`, `TRANSCRIBE_SHUTDOWN_SECRET=<64-hex>`,
  `TRANSCRIBE_BASE_DIR=%LOCALAPPDATA%/Transcribe/data`,
  `HF_HOME`/`XDG_CACHE_HOME=<base>/model_cache`, `FFMPEG_PATH=<abs ffmpeg.exe>`,
  `PATH` prepended with the ffmpeg dir, plus `HF_TOKEN` when stored.
  (`PYTHONUNBUFFERED=1` / `PYTHONDONTWRITEBYTECODE=1` are added as
  implementation requirements.)
- Handshake: backend prints `TAURI_READY {"port":..,"protocol":1,"backend_version":".."}`
  on stdout once bound; the shell then polls `GET /healthz` until
  `{"status":"ok",...}` (worker may still be `"loading"`) before navigating
  the WebView to `http://127.0.0.1:<port>/`.
- System audio: backend prints `TAURI_EVENT {"capture":"start"|"stop","session_id":".."}`;
  the shell runs WASAPI loopback (cpal), converts to 16 kHz mono s16le and
  feeds 2048-sample (4096-byte) binary frames over a second, PCM-only WS
  client on `ws://127.0.0.1:<port>/live/ws` (no Origin header, no text frames).
- Shutdown: `POST /internal/shutdown` with `X-Shutdown-Token: <secret>`,
  wait ≤10 s, `TerminateProcess` fallback; a Job Object with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (assigned while the child is
  `CREATE_SUSPENDED`, before resume) reaps the whole tree on any exit path.

## 未検証 items (all of them — nothing here has been executed)

1. **The crate does not even have a confirmed successful `cargo check`** —
   authored offline on Linux; windows-rs / webview2-com / tauri / cpal /
   tokio-tungstenite API signatures are from memory and may need mechanical
   fixes.
2. `process.rs`: CreateProcessW + pipe inheritance + CREATE_SUSPENDED → Job
   Object → ResumeThread sequence; env-block layout; graceful-shutdown timing.
3. `webview.rs`: `with_webview`/`controller()` access, `add_PermissionRequested`
   COM wiring, `WebviewWindow::navigate`, `on_navigation` behavior for the
   bootstrap → 127.0.0.1 transition.
4. `capture.rs`: cpal WASAPI loopback via `build_input_stream` on the default
   *output* device (fallback: the `wasapi` crate), device formats, the linear
   resampler's audio quality, WS reconnect behavior.
5. `downloader.rs`: Range-resume across the GitHub Releases redirect chain,
   zip extraction of a python-build-standalone tree, atomic rename semantics
   on the target volume.
6. `tauri.conf.json`: resource-map paths at bundle time, NSIS currentUser
   install, downloadBootstrapper flow, CSP on the bootstrap page,
   `createUpdaterArtifacts` signing requirement.
7. Crate versions in `Cargo.toml` (unpinned majors) and the `windows`/
   `webview2-com` ↔ wry coupling.
8. `icons/icon.ico` is a generated 32×32 placeholder; replace with real
   branding (NSIS also wants larger sizes for a polished installer).

Verification happens in Phase C (windows-2022 CI: build → install → first-run
download → `/healthz` → orphan-process check) plus a manual smoke test for
mic permission and WASAPI loopback on real hardware.
