//! Transcribe desktop shell (Tauri v2, Windows x64).
//!
//! Responsibilities of the Rust shell (kept deliberately minimal):
//! - first-run download of the Python backend runtime (downloader.rs)
//! - sidecar lifecycle: spawn/Job Object/handshake/shutdown (process.rs)
//! - WebView navigation policy + WebView2 mic permission (webview.rs)
//! - native WASAPI loopback capture -> /live/ws PCM feeder (capture.rs)
//! - setup commands for the bootstrap page (disk space, HF token in the
//!   Windows Credential Manager, download/start orchestration)
//!
//! Model downloads themselves happen inside the Python backend; after
//! navigation the backend's own UI reports that progress over SSE.
//!
//! 【未検証】The entire crate is authored on WSL2 Linux and has never been
//! compiled for (or run on) Windows. See README.md for the verification list.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod capture;
mod downloader;
mod process;
mod webview;

use std::ffi::OsString;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU16, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use rand::RngCore;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, RunEvent, State};

const KEYRING_SERVICE: &str = "Transcribe";
const KEYRING_USER: &str = "hf_token";
/// How long to wait for the TAURI_READY stdout line after spawn.
const READY_TIMEOUT: Duration = Duration::from_secs(60);
/// How long to poll /healthz after TAURI_READY.
const HEALTH_TIMEOUT: Duration = Duration::from_secs(60);
const HEALTH_POLL_INTERVAL: Duration = Duration::from_millis(500);

// ---------------------------------------------------------------------------
// Paths / state
// ---------------------------------------------------------------------------

/// Fixed on-disk layout under %LOCALAPPDATA%/Transcribe (see Design):
///   data/            -> TRANSCRIBE_BASE_DIR (backend-owned)
///   data/model_cache -> HF_HOME / XDG_CACHE_HOME
///   runtimes/cpu-<ver>/ , current-runtime.json
///   logs/
pub struct AppPaths {
    pub root: PathBuf,
    pub data_dir: PathBuf,
    pub model_cache_dir: PathBuf,
    pub runtimes_dir: PathBuf,
    pub logs_dir: PathBuf,
    pub resource_dir: PathBuf,
}

impl AppPaths {
    fn resolve(app: &AppHandle) -> Result<Self, String> {
        // Contract fixes the root at %LOCALAPPDATA%/Transcribe (not the
        // identifier-derived Tauri dir). Fallback to Tauri's local-data dir
        // only if LOCALAPPDATA is missing (non-Windows dev host).
        let root = std::env::var_os("LOCALAPPDATA")
            .map(|d| PathBuf::from(d).join("Transcribe"))
            .or_else(|| {
                app.path()
                    .app_local_data_dir()
                    .ok()
                    .map(|d| d.join("Transcribe"))
            })
            .ok_or("could not resolve %LOCALAPPDATA%")?;
        let resource_dir = app
            .path()
            .resource_dir()
            .map_err(|e| format!("resource dir: {e}"))?;
        Ok(Self {
            data_dir: root.join("data"),
            model_cache_dir: root.join("data").join("model_cache"),
            runtimes_dir: root.join("runtimes"),
            logs_dir: root.join("logs"),
            root,
            resource_dir,
        })
    }

    fn ensure_dirs(&self) -> Result<(), String> {
        for dir in [
            &self.root,
            &self.data_dir,
            &self.model_cache_dir,
            &self.runtimes_dir,
            &self.logs_dir,
        ] {
            std::fs::create_dir_all(dir)
                .map_err(|e| format!("failed to create {}: {e}", dir.display()))?;
        }
        Ok(())
    }

    fn ffmpeg_exe(&self) -> PathBuf {
        self.resource_dir.join("ffmpeg").join("ffmpeg.exe")
    }

    fn main_py(&self) -> PathBuf {
        self.resource_dir.join("main.py")
    }
}

pub struct AppState {
    pub paths: AppPaths,
    /// Live backend handle (present while the sidecar runs).
    pub backend: Mutex<Option<process::BackendHandle>>,
    /// Backend port, 0 until TAURI_READY. Read by the capture controller.
    pub port: Arc<AtomicU16>,
    /// Exact origin the WebView may navigate to / request mic for.
    pub allowed_origin: webview::AllowedOrigin,
    /// Command channel into the system-audio capture controller.
    pub capture_tx: tokio::sync::mpsc::UnboundedSender<capture::CaptureCommand>,
    download_running: AtomicBool,
    backend_starting: AtomicBool,
}

#[derive(Serialize)]
struct SetupStatus {
    runtime_installed: bool,
    runtime_version: Option<String>,
    manifest_version: Option<String>,
    manifest_size: Option<u64>,
    disk_free: Option<u64>,
    has_hf_token: bool,
    backend_running: bool,
}

#[derive(Clone, Serialize)]
struct BackendStatusEvent {
    stage: &'static str,
    message: Option<String>,
}

fn emit_backend_status(app: &AppHandle, stage: &'static str, message: Option<String>) {
    let _ = app.emit("backend-status", BackendStatusEvent { stage, message });
}

// ---------------------------------------------------------------------------
// Commands (bootstrap page IPC surface)
// ---------------------------------------------------------------------------

#[tauri::command]
fn get_setup_status(state: State<'_, AppState>) -> SetupStatus {
    let installed = downloader::installed_runtime(&state.paths);
    let manifest = downloader::read_manifest(&state.paths).ok();
    SetupStatus {
        runtime_installed: installed.is_some(),
        runtime_version: installed.map(|(cur, _)| cur.version),
        manifest_version: manifest.as_ref().map(|m| m.version.clone()),
        manifest_size: manifest.as_ref().map(|m| m.size),
        disk_free: disk_free_bytes(&state.paths.root).ok(),
        has_hf_token: hf_token_entry()
            .and_then(|e| e.get_password().map_err(|e| e.to_string()))
            .is_ok(),
        backend_running: state.backend.lock().map(|g| g.is_some()).unwrap_or(false),
    }
}

#[tauri::command]
fn check_disk_space(state: State<'_, AppState>) -> Result<u64, String> {
    disk_free_bytes(&state.paths.root)
}

#[tauri::command]
fn save_hf_token(token: String) -> Result<(), String> {
    let token = token.trim();
    if token.is_empty() {
        return Err("token must not be empty".into());
    }
    // Stored in the Windows Credential Manager (keyring windows-native
    // backend), never on disk in plain text.
    hf_token_entry()?
        .set_password(token)
        .map_err(|e| format!("failed to store token in Credential Manager: {e}"))
}

#[tauri::command]
fn delete_hf_token() -> Result<(), String> {
    match hf_token_entry()?.delete_credential() {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(format!("failed to delete token: {e}")),
    }
}

#[tauri::command]
fn open_external(url: String) -> Result<(), String> {
    let parsed = url::Url::parse(&url).map_err(|e| format!("invalid url: {e}"))?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err("only http(s) links may be opened".into());
    }
    tauri_plugin_opener::open_url(parsed.as_str(), None::<String>)
        .map_err(|e| format!("failed to open browser: {e}"))
}

#[tauri::command]
async fn start_runtime_download(app: AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    if state
        .download_running
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        return Err("ダウンロードは既に実行中です".into());
    }
    let result = downloader::download_and_install(&app, &state.paths).await;
    state.download_running.store(false, Ordering::SeqCst);
    result
}

#[tauri::command]
async fn start_backend(app: AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    if state
        .backend_starting
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        return Err("起動処理は既に実行中です".into());
    }
    let result = start_backend_inner(&app, &state).await;
    state.backend_starting.store(false, Ordering::SeqCst);
    if let Err(msg) = &result {
        emit_backend_status(&app, "failed", Some(msg.clone()));
    }
    result
}

async fn start_backend_inner(app: &AppHandle, state: &State<'_, AppState>) -> Result<(), String> {
    if state.backend.lock().unwrap().is_some() {
        // Already running: just (re)navigate.
        let port = state.port.load(Ordering::SeqCst);
        if port != 0 {
            return webview::navigate_to_backend(app, &state.allowed_origin, port);
        }
        return Err("バックエンドは起動処理中です".into());
    }

    let (_, python_exe) = downloader::installed_runtime(&state.paths)
        .ok_or("ランタイムが未インストールです。先にダウンロードしてください。")?;

    let ffmpeg_exe = state.paths.ffmpeg_exe();
    if !ffmpeg_exe.is_file() {
        return Err(format!(
            "ffmpeg.exe がリソース内に見つかりません: {}",
            ffmpeg_exe.display()
        ));
    }
    let main_py = state.paths.main_py();
    if !main_py.is_file() {
        return Err(format!(
            "main.py がリソース内に見つかりません: {}",
            main_py.display()
        ));
    }

    // HF token from the Credential Manager (optional).
    let hf_token = hf_token_entry()
        .ok()
        .and_then(|e| e.get_password().ok());

    // 32 random bytes -> 64 hex chars.
    let mut secret_bytes = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut secret_bytes);
    let secret: String = secret_bytes.iter().map(|b| format!("{b:02x}")).collect();

    let env: Vec<(OsString, OsString)> = process::contract_env(
        &state.paths.data_dir,
        &state.paths.model_cache_dir,
        &ffmpeg_exe,
        hf_token.as_deref(),
    );
    let spec = process::SpawnSpec {
        python_exe,
        main_py,
        cwd: state.paths.resource_dir.clone(),
        env,
        log_dir: state.paths.logs_dir.clone(),
    };

    emit_backend_status(app, "spawning", None);
    let (ready_tx, ready_rx) = std::sync::mpsc::channel::<process::ReadyInfo>();
    let capture_tx = state.capture_tx.clone();
    let handle = tokio::task::spawn_blocking(move || {
        process::spawn_backend(&spec, secret, ready_tx, capture_tx)
    })
    .await
    .map_err(|e| format!("spawn task panicked: {e}"))??;

    // Wait for the TAURI_READY handshake line.
    emit_backend_status(app, "waiting-ready", None);
    let ready = tokio::task::spawn_blocking(move || ready_rx.recv_timeout(READY_TIMEOUT))
        .await
        .map_err(|e| format!("ready-wait task panicked: {e}"))?;
    let ready = match ready {
        Ok(info) => info,
        Err(_) => {
            process::shutdown_blocking(&handle);
            return Err(format!(
                "バックエンドが {}s 以内に TAURI_READY を返しませんでした。ログ: {}",
                READY_TIMEOUT.as_secs(),
                state.paths.logs_dir.display()
            ));
        }
    };
    if ready.protocol != process::EXPECTED_PROTOCOL {
        process::shutdown_blocking(&handle);
        return Err(format!(
            "protocol mismatch: shell={} backend={} — シェルとバックエンドの版が一致していません",
            process::EXPECTED_PROTOCOL,
            ready.protocol
        ));
    }

    let mut handle = handle;
    handle.port = ready.port;
    handle.backend_version = ready.backend_version.clone();

    // /healthz gate: navigate only after the HTTP layer answers. The worker
    // may still be "loading"; the backend UI itself shows that state.
    emit_backend_status(app, "waiting-health", None);
    if let Err(e) = wait_healthz(ready.port).await {
        process::shutdown_blocking(&handle);
        return Err(e);
    }

    state.port.store(ready.port, Ordering::SeqCst);
    *state.backend.lock().unwrap() = Some(handle);

    emit_backend_status(app, "navigating", None);
    webview::navigate_to_backend(app, &state.allowed_origin, ready.port)?;
    emit_backend_status(app, "running", Some(format!("backend {}", ready.backend_version)));
    Ok(())
}

/// Polls GET /healthz until {"status":"ok"} (worker may still be loading).
async fn wait_healthz(port: u16) -> Result<(), String> {
    let client = reqwest::Client::builder()
        .no_proxy() // never route loopback through a system proxy
        .timeout(Duration::from_secs(2))
        .build()
        .map_err(|e| e.to_string())?;
    let url = format!("http://127.0.0.1:{port}/healthz");
    let deadline = std::time::Instant::now() + HEALTH_TIMEOUT;
    let mut last_err = String::from("no attempt made");
    while std::time::Instant::now() < deadline {
        match client.get(&url).send().await {
            Ok(resp) if resp.status().is_success() => {
                match resp.json::<serde_json::Value>().await {
                    Ok(body) if body.get("status").and_then(|s| s.as_str()) == Some("ok") => {
                        return Ok(());
                    }
                    Ok(body) => last_err = format!("unexpected healthz body: {body}"),
                    Err(e) => last_err = format!("healthz body parse error: {e}"),
                }
            }
            Ok(resp) => last_err = format!("healthz HTTP {}", resp.status()),
            Err(e) => last_err = format!("healthz request error: {e}"),
        }
        tokio::time::sleep(HEALTH_POLL_INTERVAL).await;
    }
    Err(format!(
        "/healthz が {}s 以内に ok になりませんでした（最終エラー: {last_err}）",
        HEALTH_TIMEOUT.as_secs()
    ))
}

#[tauri::command]
fn backend_state(state: State<'_, AppState>) -> String {
    match state.backend.lock().unwrap().as_ref() {
        Some(h) => format!("running (port {}, backend {})", h.port, h.backend_version),
        None if state.backend_starting.load(Ordering::SeqCst) => "starting".into(),
        None => "stopped".into(),
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn hf_token_entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER)
        .map_err(|e| format!("Credential Manager unavailable: {e}"))
}

/// Free bytes available to the current user on the volume containing `path`.
#[cfg(windows)]
fn disk_free_bytes(path: &std::path::Path) -> Result<u64, String> {
    use windows::core::PCWSTR;
    use windows::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;
    let wide: Vec<u16> = path
        .as_os_str()
        .to_string_lossy()
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect();
    let mut free: u64 = 0;
    // SAFETY: wide is NUL-terminated and outlives the call. 【未検証】
    unsafe {
        GetDiskFreeSpaceExW(PCWSTR(wide.as_ptr()), Some(&mut free), None, None)
            .map_err(|e| format!("GetDiskFreeSpaceExW: {e}"))?;
    }
    Ok(free)
}

#[cfg(not(windows))]
fn disk_free_bytes(_path: &std::path::Path) -> Result<u64, String> {
    Err("disk space check is only implemented for Windows".into())
}

/// Graceful, idempotent backend teardown; used on every exit path.
fn shutdown_backend(app: &AppHandle) {
    if let Some(state) = app.try_state::<AppState>() {
        let taken = state.backend.lock().ok().and_then(|mut g| g.take());
        if let Some(handle) = taken {
            // Contract: POST /internal/shutdown, wait <=10 s, kill, close Job.
            process::shutdown_blocking(&handle);
        }
        state.port.store(0, Ordering::SeqCst);
    }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let handle = app.handle().clone();
            let paths = AppPaths::resolve(&handle).map_err(std::io::Error::other)?;
            paths.ensure_dirs().map_err(std::io::Error::other)?;

            let port = Arc::new(AtomicU16::new(0));
            let allowed_origin: webview::AllowedOrigin = Arc::new(std::sync::RwLock::new(None));
            // Capture controller runs for the app lifetime; commands arrive
            // from process.rs (TAURI_EVENT stdout lines).
            let capture_tx = capture::spawn_controller(port.clone());

            app.manage(AppState {
                paths,
                backend: Mutex::new(None),
                port,
                allowed_origin: allowed_origin.clone(),
                capture_tx,
                download_running: AtomicBool::new(false),
                backend_starting: AtomicBool::new(false),
            });

            webview::create_main_window(&handle, allowed_origin)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_setup_status,
            check_disk_space,
            save_hf_token,
            delete_hf_token,
            open_external,
            start_runtime_download,
            start_backend,
            backend_state,
        ])
        .build(tauri::generate_context!())
        .expect("failed to build tauri application");

    app.run(|app_handle, event| match event {
        // Blocks the event loop for at most the 10 s contract window while the
        // backend shuts down; acceptable because the window is already gone.
        RunEvent::ExitRequested { .. } => shutdown_backend(app_handle),
        // Belt and braces: also runs on the final exit event. Idempotent —
        // the handle was already taken above in the normal path, and the Job
        // Object (KILL_ON_JOB_CLOSE) covers abnormal terminations.
        RunEvent::Exit => shutdown_backend(app_handle),
        _ => {}
    });
}
