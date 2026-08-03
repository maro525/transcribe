//! Python sidecar process lifecycle (Windows).
//!
//! Frozen contract with the Python backend (do not change without bumping
//! `protocol`):
//! - Spawn: `<runtime_dir>/python.exe <resource_dir>/main.py` with env
//!   TRANSCRIBE_DYNAMIC_PORT=1, TRANSCRIBE_SHUTDOWN_SECRET=<hex>,
//!   TRANSCRIBE_BASE_DIR=%LOCALAPPDATA%/Transcribe/data,
//!   HF_HOME / XDG_CACHE_HOME=<base>/model_cache, FFMPEG_PATH=<abs ffmpeg.exe>,
//!   PATH prepended with the ffmpeg dir (+ HF_TOKEN when stored).
//! - Python prints exactly once on stdout when bound:
//!     TAURI_READY {"port": <int>, "protocol": 1, "backend_version": "<str>"}
//!   and on system-audio transitions:
//!     TAURI_EVENT {"capture": "start"|"stop", "session_id": "<str>"}
//! - Graceful exit: POST /internal/shutdown with `X-Shutdown-Token: <secret>`,
//!   wait up to 75 s for process exit (Python finalize can take up to 60 s),
//!   then TerminateProcess. If the handshake never completed (port == 0) the
//!   POST/wait is skipped and the process is terminated immediately. The Job
//!   Object (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE) additionally kills the whole
//!   process tree (incl. ffmpeg grandchildren) on ANY shell exit path.
//! - Env hardening (not part of the wire protocol): WEB_HOST is pinned to
//!   127.0.0.1 and PYTHONPATH / PYTHONHOME / PYTHONSTARTUP are overridden to
//!   empty strings so inherited values cannot leak into the sidecar.
//!
//! Spawn sequence (race-free job assignment):
//!   CreateProcessW(CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT)
//!     -> CreateJobObjectW + SetInformationJobObject(KILL_ON_JOB_CLOSE)
//!     -> AssignProcessToJobObject
//!     -> ResumeThread
//! CREATE_SUSPENDED is required because std::process cannot hand us the primary
//! thread handle needed for ResumeThread; we therefore go through CreateProcessW
//! directly via the `windows` crate.
//!
//! 【未検証】This entire module is unverified: authored on WSL2 Linux, never
//! compiled against the Windows toolchain nor executed. windows-rs call
//! signatures were written from memory of the 0.6x API surface and must be
//! checked on the first real Windows build.

use std::collections::BTreeMap;
use std::ffi::OsString;
use std::fs;
use std::io::{BufRead, BufReader, Read, Write as IoWrite};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::sync::mpsc::Sender as StdSender;
use std::time::Duration;

use serde::Deserialize;
use tokio::sync::mpsc::UnboundedSender;

use crate::capture::CaptureCommand;

pub const READY_PREFIX: &str = "TAURI_READY ";
pub const EVENT_PREFIX: &str = "TAURI_EVENT ";
/// Protocol version this shell speaks (must match `/healthz` and TAURI_READY).
pub const EXPECTED_PROTOCOL: u64 = 1;

/// Python finalize (live-session flush + model teardown) can take up to 60 s;
/// give it headroom before the TerminateProcess fallback.
const SHUTDOWN_WAIT_MS: u32 = 75_000;
const LOG_MAX_BYTES: u64 = 5 * 1024 * 1024;
const LOG_KEEP: usize = 3;

/// Payload of the `TAURI_READY` stdout line.
#[derive(Debug, Clone, Deserialize)]
pub struct ReadyInfo {
    pub port: u16,
    pub protocol: u64,
    pub backend_version: String,
}

/// Payload of a `TAURI_EVENT` stdout line.
#[derive(Debug, Clone, Deserialize)]
struct TauriEventLine {
    capture: String,
    #[serde(default)]
    session_id: Option<String>,
}

/// Everything needed to launch the sidecar.
pub struct SpawnSpec {
    /// `<runtime_dir>/python.exe`
    pub python_exe: PathBuf,
    /// `<resource_dir>/main.py`
    pub main_py: PathBuf,
    /// Working directory (resource dir, so `import src...` resolves).
    pub cwd: PathBuf,
    /// Extra/overriding environment variables (contract env).
    pub env: Vec<(OsString, OsString)>,
    /// Directory for rotated backend-stdout.log / backend-stderr.log.
    pub log_dir: PathBuf,
}

/// Live handle to a spawned backend. Raw HANDLEs are wrapped so the struct is
/// Send; they are owned exclusively by this struct and closed on shutdown.
pub struct BackendHandle {
    pub pid: u32,
    pub secret: String,
    /// Set by the caller once TAURI_READY arrives.
    pub port: u16,
    pub backend_version: String,
    #[cfg(windows)]
    process: RawHandleOwned,
    #[cfg(windows)]
    job: RawHandleOwned,
}

#[cfg(windows)]
struct RawHandleOwned(isize);
// SAFETY: a Win32 HANDLE is just a kernel object reference; it is valid on any
// thread of the owning process.
#[cfg(windows)]
unsafe impl Send for RawHandleOwned {}
#[cfg(windows)]
unsafe impl Sync for RawHandleOwned {}

// ---------------------------------------------------------------------------
// Rotating log writer (size-based, backend-stdout.log -> .1 -> .2)
// ---------------------------------------------------------------------------

struct RotatingLog {
    path: PathBuf,
    file: Option<fs::File>,
    written: u64,
}

impl RotatingLog {
    fn new(path: PathBuf) -> Self {
        let written = fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
        Self { path, file: None, written }
    }

    fn write_line(&mut self, line: &str) {
        if self.written >= LOG_MAX_BYTES {
            self.rotate();
        }
        if self.file.is_none() {
            self.file = fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&self.path)
                .ok();
        }
        if let Some(f) = self.file.as_mut() {
            let _ = writeln!(f, "{line}");
            self.written += line.len() as u64 + 1;
        }
    }

    fn rotate(&mut self) {
        self.file = None;
        // backend.log.(KEEP-1) is discarded; shift the rest up by one.
        let numbered = |n: usize| -> PathBuf {
            PathBuf::from(format!("{}.{n}", self.path.display()))
        };
        let _ = fs::remove_file(numbered(LOG_KEEP - 1));
        for n in (1..LOG_KEEP - 1).rev() {
            let _ = fs::rename(numbered(n), numbered(n + 1));
        }
        let _ = fs::rename(&self.path, numbered(1));
        self.written = 0;
    }
}

// ---------------------------------------------------------------------------
// stdout/stderr drain threads
// ---------------------------------------------------------------------------

/// Drains the sidecar stdout: every line goes to the rotating log; contract
/// lines are parsed and dispatched.
fn drain_stdout<R: Read + Send + 'static>(
    reader: R,
    log_path: PathBuf,
    ready_tx: StdSender<ReadyInfo>,
    capture_tx: UnboundedSender<CaptureCommand>,
) {
    std::thread::Builder::new()
        .name("backend-stdout".into())
        .spawn(move || {
            let mut log = RotatingLog::new(log_path);
            let reader = BufReader::new(reader);
            for line in reader.lines() {
                let Ok(line) = line else { break };
                log.write_line(&line);
                if let Some(json) = line.strip_prefix(READY_PREFIX) {
                    match serde_json::from_str::<ReadyInfo>(json.trim()) {
                        Ok(info) => {
                            // Receiver may be gone after startup; ignore.
                            let _ = ready_tx.send(info);
                        }
                        Err(e) => log.write_line(&format!(
                            "[shell] failed to parse TAURI_READY line: {e}"
                        )),
                    }
                } else if let Some(json) = line.strip_prefix(EVENT_PREFIX) {
                    match serde_json::from_str::<TauriEventLine>(json.trim()) {
                        Ok(ev) => {
                            let cmd = match ev.capture.as_str() {
                                "start" => Some(CaptureCommand::Start {
                                    session_id: ev.session_id.unwrap_or_default(),
                                }),
                                "stop" => Some(CaptureCommand::Stop),
                                other => {
                                    log.write_line(&format!(
                                        "[shell] unknown TAURI_EVENT capture value: {other}"
                                    ));
                                    None
                                }
                            };
                            if let Some(cmd) = cmd {
                                let _ = capture_tx.send(cmd);
                            }
                        }
                        Err(e) => log.write_line(&format!(
                            "[shell] failed to parse TAURI_EVENT line: {e}"
                        )),
                    }
                }
            }
            log.write_line("[shell] stdout pipe closed");
        })
        .expect("spawn backend-stdout drain thread");
}

fn drain_stderr<R: Read + Send + 'static>(reader: R, log_path: PathBuf) {
    std::thread::Builder::new()
        .name("backend-stderr".into())
        .spawn(move || {
            let mut log = RotatingLog::new(log_path);
            let reader = BufReader::new(reader);
            for line in reader.lines() {
                let Ok(line) = line else { break };
                log.write_line(&line);
            }
            log.write_line("[shell] stderr pipe closed");
        })
        .expect("spawn backend-stderr drain thread");
}

// ---------------------------------------------------------------------------
// Spawn (Windows)
// ---------------------------------------------------------------------------

/// Spawns the backend. Returns a handle whose `port`/`backend_version` are
/// still empty; the caller waits on `ready_tx`'s receiving end (TAURI_READY)
/// and then fills them in.
#[cfg(windows)]
pub fn spawn_backend(
    spec: &SpawnSpec,
    secret: String,
    ready_tx: StdSender<ReadyInfo>,
    capture_tx: UnboundedSender<CaptureCommand>,
) -> Result<BackendHandle, String> {
    use windows::core::{PCWSTR, PWSTR};
    use windows::Win32::Foundation::{
        CloseHandle, SetHandleInformation, HANDLE, HANDLE_FLAGS, HANDLE_FLAG_INHERIT,
    };
    use windows::Win32::Security::SECURITY_ATTRIBUTES;
    use windows::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows::Win32::System::Pipes::CreatePipe;
    use windows::Win32::System::Threading::{
        CreateProcessW, ResumeThread, CREATE_NO_WINDOW, CREATE_SUSPENDED,
        CREATE_UNICODE_ENVIRONMENT, PROCESS_INFORMATION, STARTF_USESTDHANDLES, STARTUPINFOW,
    };

    fs::create_dir_all(&spec.log_dir)
        .map_err(|e| format!("failed to create log dir {}: {e}", spec.log_dir.display()))?;

    // --- pipes: child writes stdout/stderr, we read -----------------------
    // Write ends must be inheritable, read ends must NOT be.
    let sa = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: std::ptr::null_mut(),
        bInheritHandle: true.into(),
    };

    let mut stdout_read = HANDLE::default();
    let mut stdout_write = HANDLE::default();
    let mut stderr_read = HANDLE::default();
    let mut stderr_write = HANDLE::default();

    // SAFETY: standard pipe-inheritance dance; handles closed below on every
    // path via `close` helper. 【未検証】exact windows-rs 0.6x signatures.
    unsafe {
        CreatePipe(&mut stdout_read, &mut stdout_write, Some(&sa), 0)
            .map_err(|e| format!("CreatePipe(stdout): {e}"))?;
        CreatePipe(&mut stderr_read, &mut stderr_write, Some(&sa), 0)
            .map_err(|e| format!("CreatePipe(stderr): {e}"))?;
        SetHandleInformation(stdout_read, HANDLE_FLAG_INHERIT.0, HANDLE_FLAGS(0))
            .map_err(|e| format!("SetHandleInformation(stdout): {e}"))?;
        SetHandleInformation(stderr_read, HANDLE_FLAG_INHERIT.0, HANDLE_FLAGS(0))
            .map_err(|e| format!("SetHandleInformation(stderr): {e}"))?;
    }

    let close = |h: HANDLE| unsafe {
        let _ = CloseHandle(h);
    };

    // --- command line + environment --------------------------------------
    // "C:\...\python.exe" "C:\...\main.py"
    let cmdline = format!(
        "{} {}",
        quote_windows_arg(&spec.python_exe.to_string_lossy()),
        quote_windows_arg(&spec.main_py.to_string_lossy()),
    );
    // CreateProcessW may modify the command-line buffer; it must be mutable.
    let mut cmdline_w: Vec<u16> = to_wide(&cmdline);

    let mut env: Vec<(OsString, OsString)> = spec.env.clone();
    env.push(("TRANSCRIBE_SHUTDOWN_SECRET".into(), secret.clone().into()));
    let env_block = build_env_block(&env);
    let cwd_w = to_wide(&spec.cwd.to_string_lossy());

    let mut si = STARTUPINFOW::default();
    si.cb = std::mem::size_of::<STARTUPINFOW>() as u32;
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdOutput = stdout_write;
    si.hStdError = stderr_write;
    // hStdInput left NULL: the backend never reads stdin.

    let mut pi = PROCESS_INFORMATION::default();

    // --- CreateProcessW (suspended) ---------------------------------------
    // SAFETY: all pointers live for the duration of the call. 【未検証】
    let create_res = unsafe {
        CreateProcessW(
            PCWSTR::null(),
            Some(PWSTR(cmdline_w.as_mut_ptr())),
            None,
            None,
            true, // inherit handles (for the two pipe write ends)
            CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
            Some(env_block.as_ptr() as *const core::ffi::c_void),
            PCWSTR(cwd_w.as_ptr()),
            &si,
            &mut pi,
        )
    };
    // Parent must always close its copies of the write ends, or the read side
    // never sees EOF.
    close(stdout_write);
    close(stderr_write);
    if let Err(e) = create_res {
        close(stdout_read);
        close(stderr_read);
        return Err(format!("CreateProcessW failed for {cmdline}: {e}"));
    }

    // --- Job Object: assign BEFORE resume ---------------------------------
    // Any failure here must terminate the still-suspended child, or it would
    // leak as a permanently suspended process.
    let kill_suspended = |reason: String| -> String {
        unsafe {
            let _ = windows::Win32::System::Threading::TerminateProcess(pi.hProcess, 1);
            close(pi.hThread);
            close(pi.hProcess);
            close(stdout_read);
            close(stderr_read);
        }
        reason
    };
    let job = unsafe { CreateJobObjectW(None, PCWSTR::null()) }
        .map_err(|e| kill_suspended(format!("CreateJobObjectW: {e}")))?;
    // Once the job exists, every early-return path must also close it, or the
    // job handle leaks (and with it, later, KILL_ON_JOB_CLOSE never fires).
    let kill_suspended_with_job = |reason: String| -> String {
        close(job);
        kill_suspended(reason)
    };
    let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    // SAFETY: struct is fully initialized, size passed explicitly. 【未検証】
    unsafe {
        SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        )
        .map_err(|e| kill_suspended_with_job(format!("SetInformationJobObject: {e}")))?;
        AssignProcessToJobObject(job, pi.hProcess)
            .map_err(|e| kill_suspended_with_job(format!("AssignProcessToJobObject: {e}")))?;
        if ResumeThread(pi.hThread) == u32::MAX {
            return Err(kill_suspended_with_job("ResumeThread failed".into()));
        }
        close(pi.hThread);
    }

    // --- drain threads -----------------------------------------------------
    // SAFETY: read handles are owned exclusively by the Files from here on.
    let (stdout_file, stderr_file) = unsafe {
        use std::os::windows::io::FromRawHandle;
        (
            fs::File::from_raw_handle(stdout_read.0 as _),
            fs::File::from_raw_handle(stderr_read.0 as _),
        )
    };
    drain_stdout(
        stdout_file,
        spec.log_dir.join("backend-stdout.log"),
        ready_tx,
        capture_tx,
    );
    drain_stderr(stderr_file, spec.log_dir.join("backend-stderr.log"));

    Ok(BackendHandle {
        pid: pi.dwProcessId,
        secret,
        port: 0,
        backend_version: String::new(),
        process: RawHandleOwned(pi.hProcess.0 as isize),
        job: RawHandleOwned(job.0 as isize),
    })
}

/// Non-Windows build: the sidecar contract (Job Objects, WASAPI, python.exe)
/// is Windows-only. This is a hard error, not a stub of the behavior.
#[cfg(not(windows))]
pub fn spawn_backend(
    _spec: &SpawnSpec,
    _secret: String,
    _ready_tx: StdSender<ReadyInfo>,
    _capture_tx: UnboundedSender<CaptureCommand>,
) -> Result<BackendHandle, String> {
    Err("the Transcribe desktop shell only supports Windows (x86_64-pc-windows-msvc)".into())
}

// ---------------------------------------------------------------------------
// Graceful shutdown
// ---------------------------------------------------------------------------

/// Contract shutdown: POST /internal/shutdown (X-Shutdown-Token), wait up to
/// 75 s (Python finalize can take up to 60 s), TerminateProcess fallback, then
/// close the Job handle so KILL_ON_JOB_CLOSE reaps any surviving descendants
/// (ffmpeg etc.).
///
/// If the TAURI_READY handshake never completed (`port == 0`) there is nothing
/// to shut down gracefully: skip the HTTP POST and the long wait entirely and
/// go straight to TerminateProcess + Job close.
///
/// Deliberately uses a hand-rolled HTTP/1.1 request over std TcpStream so it
/// can run synchronously from `RunEvent::ExitRequested` without touching the
/// async runtime.
pub fn shutdown_blocking(handle: &BackendHandle) {
    let graceful = handle.port != 0;
    if graceful {
        let _ = post_shutdown(handle.port, &handle.secret);
    }

    #[cfg(windows)]
    unsafe {
        use windows::Win32::Foundation::{CloseHandle, HANDLE, WAIT_OBJECT_0};
        use windows::Win32::System::Threading::{TerminateProcess, WaitForSingleObject};

        let process = HANDLE(handle.process.0 as _);
        let job = HANDLE(handle.job.0 as _);

        if graceful {
            if WaitForSingleObject(process, SHUTDOWN_WAIT_MS) != WAIT_OBJECT_0 {
                // Graceful path failed or timed out: hard kill.
                let _ = TerminateProcess(process, 1);
                let _ = WaitForSingleObject(process, 2_000);
            }
        } else {
            // Handshake never completed: immediate hard kill.
            let _ = TerminateProcess(process, 1);
            let _ = WaitForSingleObject(process, 2_000);
        }
        // Closing the job handle fires KILL_ON_JOB_CLOSE for the whole tree.
        let _ = CloseHandle(job);
        let _ = CloseHandle(process);
    }
}

// ---------------------------------------------------------------------------
// Backend death detection
// ---------------------------------------------------------------------------

/// Spawns a monitor thread that blocks on the backend process handle
/// (`WaitForSingleObject(..., INFINITE)`) and calls `on_exit` with the exit
/// code (if retrievable) once the process terminates — for ANY reason,
/// including our own graceful/hard shutdown; the caller is responsible for
/// distinguishing requested shutdowns (see AppState.shutdown_requested).
///
/// The process handle is duplicated so the monitor is independent of the
/// `BackendHandle`'s lifetime. 【未検証】windows-rs signatures from memory.
#[cfg(windows)]
pub fn spawn_exit_monitor(
    handle: &BackendHandle,
    on_exit: Box<dyn FnOnce(Option<u32>) + Send + 'static>,
) -> Result<(), String> {
    use windows::Win32::Foundation::{
        CloseHandle, DuplicateHandle, DUPLICATE_SAME_ACCESS, HANDLE,
    };
    use windows::Win32::System::Threading::{
        GetCurrentProcess, GetExitCodeProcess, WaitForSingleObject, INFINITE,
    };

    let process = HANDLE(handle.process.0 as _);
    let mut dup = HANDLE::default();
    // SAFETY: both handles are valid; the duplicate is owned by the monitor
    // thread and closed there. 【未検証】
    unsafe {
        DuplicateHandle(
            GetCurrentProcess(),
            process,
            GetCurrentProcess(),
            &mut dup,
            0,
            false,
            DUPLICATE_SAME_ACCESS,
        )
        .map_err(|e| format!("DuplicateHandle(backend process): {e}"))?;
    }
    let dup = RawHandleOwned(dup.0 as isize);
    std::thread::Builder::new()
        .name("backend-exit-monitor".into())
        .spawn(move || {
            let h = HANDLE(dup.0 as _);
            // SAFETY: h is the duplicated, thread-owned handle. 【未検証】
            let code = unsafe {
                let _ = WaitForSingleObject(h, INFINITE);
                let mut code: u32 = 0;
                let got = GetExitCodeProcess(h, &mut code).is_ok();
                let _ = CloseHandle(h);
                got.then_some(code)
            };
            on_exit(code);
        })
        .map_err(|e| format!("spawn backend-exit-monitor thread: {e}"))?;
    Ok(())
}

/// Non-Windows build: no sidecar, nothing to monitor.
#[cfg(not(windows))]
pub fn spawn_exit_monitor(
    _handle: &BackendHandle,
    _on_exit: Box<dyn FnOnce(Option<u32>) + Send + 'static>,
) -> Result<(), String> {
    Ok(())
}

fn post_shutdown(port: u16, secret: &str) -> std::io::Result<()> {
    let mut stream = TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}").parse().unwrap(),
        Duration::from_secs(2),
    )?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    let req = format!(
        "POST /internal/shutdown HTTP/1.1\r\n\
         Host: 127.0.0.1:{port}\r\n\
         X-Shutdown-Token: {secret}\r\n\
         Content-Length: 0\r\n\
         Connection: close\r\n\r\n"
    );
    stream.write_all(req.as_bytes())?;
    // Read (and discard) the response so the server sees a complete exchange.
    let mut buf = [0u8; 512];
    let _ = stream.read(&mut buf);
    Ok(())
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// UTF-16, NUL-terminated.
fn to_wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

/// Minimal Windows argv quoting: wrap in quotes, double up embedded quotes and
/// escape trailing backslashes. Paths we pass contain no quotes in practice.
fn quote_windows_arg(arg: &str) -> String {
    if !arg.contains([' ', '\t', '"']) {
        return arg.to_string();
    }
    let mut out = String::with_capacity(arg.len() + 2);
    out.push('"');
    let mut backslashes = 0usize;
    for c in arg.chars() {
        match c {
            '\\' => {
                backslashes += 1;
                out.push('\\');
            }
            '"' => {
                out.extend(std::iter::repeat('\\').take(backslashes + 1));
                out.push('"');
                backslashes = 0;
            }
            _ => {
                backslashes = 0;
                out.push(c);
            }
        }
    }
    out.extend(std::iter::repeat('\\').take(backslashes));
    out.push('"');
    out
}

/// Builds a CREATE_UNICODE_ENVIRONMENT block: current process env merged with
/// `extra` (extra wins), sorted case-insensitively (Windows convention),
/// "NAME=VALUE\0"... "\0" terminated.
fn build_env_block(extra: &[(OsString, OsString)]) -> Vec<u16> {
    let mut map: BTreeMap<String, (OsString, OsString)> = BTreeMap::new();
    for (k, v) in std::env::vars_os() {
        map.insert(
            k.to_string_lossy().to_uppercase(),
            (k, v),
        );
    }
    for (k, v) in extra {
        map.insert(
            k.to_string_lossy().to_uppercase(),
            (k.clone(), v.clone()),
        );
    }
    let mut block: Vec<u16> = Vec::new();
    for (_, (k, v)) in map {
        block.extend(k.to_string_lossy().encode_utf16());
        block.push('=' as u16);
        block.extend(v.to_string_lossy().encode_utf16());
        block.push(0);
    }
    block.push(0);
    block
}

/// Builds the contract environment for a spawn.
#[allow(clippy::too_many_arguments)]
pub fn contract_env(
    base_dir: &Path,
    model_cache_dir: &Path,
    ffmpeg_exe: &Path,
    hf_token: Option<&str>,
) -> Vec<(OsString, OsString)> {
    let ffmpeg_dir = ffmpeg_exe.parent().unwrap_or(Path::new("."));
    let old_path = std::env::var_os("PATH").unwrap_or_default();
    let mut new_path = ffmpeg_dir.as_os_str().to_os_string();
    new_path.push(";");
    new_path.push(&old_path);

    let mut env: Vec<(OsString, OsString)> = vec![
        ("TRANSCRIBE_DYNAMIC_PORT".into(), "1".into()),
        ("TRANSCRIBE_BASE_DIR".into(), base_dir.as_os_str().to_owned()),
        ("HF_HOME".into(), model_cache_dir.as_os_str().to_owned()),
        ("XDG_CACHE_HOME".into(), model_cache_dir.as_os_str().to_owned()),
        ("FFMPEG_PATH".into(), ffmpeg_exe.as_os_str().to_owned()),
        ("PATH".into(), new_path),
        // Not part of the frozen contract but required for it to work:
        // without unbuffered stdout the TAURI_READY / TAURI_EVENT lines sit in
        // Python's pipe buffer and the shell never sees them in time.
        ("PYTHONUNBUFFERED".into(), "1".into()),
        // The resource dir may be under Program Files-like restrictions; never
        // try to write .pyc next to the bundled sources.
        ("PYTHONDONTWRITEBYTECODE".into(), "1".into()),
        // Hardening (overrides anything inherited from the user session):
        // pin the bind host, and neutralize interpreter-hijack env vars.
        // CPython treats empty PYTHONHOME/PYTHONPATH/PYTHONSTARTUP as unset.
        ("WEB_HOST".into(), "127.0.0.1".into()),
        ("PYTHONPATH".into(), "".into()),
        ("PYTHONHOME".into(), "".into()),
        ("PYTHONSTARTUP".into(), "".into()),
    ];
    if let Some(token) = hf_token {
        env.push(("HF_TOKEN".into(), token.into()));
    }
    env
}
