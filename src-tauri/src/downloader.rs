//! First-run backend runtime downloader.
//!
//! The Python runtime (python-build-standalone + pinned site-packages) is NOT
//! bundled in the installer (NSIS 2GB cap). It is downloaded from a public
//! GitHub Releases URL declared in the bundled `backend-manifest.json`
//! (Tauri resource, produced by packaging/backend at build time):
//!
//! ```json
//! { "version": "2026.07.1",
//!   "url": "https://github.com/<org>/<repo>/releases/download/backend-2026.07.1/cpu-2026.07.1.zip",
//!   "sha256": "<hex>",
//!   "size": 734003200,
//!   "python_exe": "python.exe" }        // optional, relative to archive root
//! ```
//!
//! Flow:
//!   1. Download to `runtimes/download/cpu-<version>.zip.partial` with HTTP
//!      Range resume (existing bytes are re-hashed before resuming).
//!   2. Verify SHA-256 against the manifest.
//!   3. Extract (zip; deliberately chosen over tar.zst for stdlib-free
//!      simplicity) into `runtimes/.extract-<version>.tmp`.
//!   4. Atomic `rename` to `runtimes/cpu-<version>/` (same volume).
//!   5. Write `current-runtime.json` next to `runtimes/`.
//!   Rollback: any failure removes the temp extract dir and leaves the
//!   previous `current-runtime.json` (and any previous runtime dir) untouched.
//!   The `.partial` file is kept on download errors so a retry resumes.
//!
//! Progress is emitted to the bootstrap window as `runtime-download-progress`
//! events: `{ phase, downloaded, total, bytes_per_sec }`.
//!
//! 【未検証】Never compiled or executed (WSL2 dev host, no Windows toolchain).
//! In particular: reqwest's Range behavior across the GitHub Releases ->
//! objects.githubusercontent.com redirect is unverified.

use std::fs;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter};

use crate::AppPaths;

const PROGRESS_INTERVAL: Duration = Duration::from_millis(250);

#[derive(Debug, Clone, Deserialize)]
pub struct BackendManifest {
    pub version: String,
    pub url: String,
    pub sha256: String,
    pub size: u64,
    #[serde(default)]
    pub python_exe: Option<String>,
}

/// `current-runtime.json` — records the active runtime for atomic switch /
/// rollback. Written only after a fully verified install.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CurrentRuntime {
    pub version: String,
    /// Directory name under `runtimes/` (e.g. "cpu-2026.07.1").
    pub dir: String,
    /// Path of python.exe relative to `dir`.
    pub python_exe: String,
    pub installed_at_unix: u64,
    pub protocol: u64,
}

#[derive(Debug, Clone, Serialize)]
struct Progress {
    phase: &'static str,
    downloaded: u64,
    total: u64,
    bytes_per_sec: u64,
}

fn emit_progress(app: &AppHandle, phase: &'static str, downloaded: u64, total: u64, bps: u64) {
    let _ = app.emit(
        "runtime-download-progress",
        Progress { phase, downloaded, total, bytes_per_sec: bps },
    );
}

/// Reads the bundled backend manifest from the resource dir.
pub fn read_manifest(paths: &AppPaths) -> Result<BackendManifest, String> {
    let path = paths.resource_dir.join("backend-manifest.json");
    let data = fs::read_to_string(&path)
        .map_err(|e| format!("backend-manifest.json not found at {}: {e}", path.display()))?;
    serde_json::from_str(&data).map_err(|e| format!("backend-manifest.json is invalid: {e}"))
}

/// Returns the installed runtime (from current-runtime.json) if its python.exe
/// actually exists on disk.
pub fn installed_runtime(paths: &AppPaths) -> Option<(CurrentRuntime, PathBuf)> {
    let data = fs::read_to_string(paths.root.join("current-runtime.json")).ok()?;
    let cur: CurrentRuntime = serde_json::from_str(&data).ok()?;
    let python = paths.runtimes_dir.join(&cur.dir).join(&cur.python_exe);
    python.is_file().then_some((cur, python))
}

/// Downloads + verifies + installs the runtime described by the manifest.
/// Idempotent: returns immediately if this version is already installed.
pub async fn download_and_install(app: &AppHandle, paths: &AppPaths) -> Result<(), String> {
    let manifest = read_manifest(paths)?;
    let final_dir = paths.runtimes_dir.join(format!("cpu-{}", manifest.version));

    if let Some((cur, _)) = installed_runtime(paths) {
        if cur.version == manifest.version {
            emit_progress(app, "done", manifest.size, manifest.size, 0);
            return Ok(());
        }
    }

    let download_dir = paths.runtimes_dir.join("download");
    fs::create_dir_all(&download_dir).map_err(|e| format!("create {}: {e}", download_dir.display()))?;
    let partial = download_dir.join(format!("cpu-{}.zip.partial", manifest.version));

    // 1+2: download with resume, hashing as we go; verify.
    download_with_resume(app, &manifest, &partial).await?;

    // 3: extract to temp dir.
    let extract_tmp = paths
        .runtimes_dir
        .join(format!(".extract-{}.tmp", manifest.version));
    if extract_tmp.exists() {
        let _ = fs::remove_dir_all(&extract_tmp);
    }
    emit_progress(app, "extract", manifest.size, manifest.size, 0);
    {
        let partial_for_task = partial.clone();
        let extract_tmp_for_task = extract_tmp.clone();
        tokio::task::spawn_blocking(move || extract_zip(&partial_for_task, &extract_tmp_for_task))
            .await
            .map_err(|e| format!("extract task panicked: {e}"))?
            .inspect_err(|_| {
                // Rollback: never leave a half-extracted tree behind.
                let _ = fs::remove_dir_all(&extract_tmp);
            })?;
    }

    // 4: atomic switch.
    emit_progress(app, "install", manifest.size, manifest.size, 0);
    let python_rel = resolve_python_exe(&extract_tmp, manifest.python_exe.as_deref())
        .inspect_err(|_| {
            let _ = fs::remove_dir_all(&extract_tmp);
        })?;
    if final_dir.exists() {
        // Stale/partial previous attempt for the same version: replace it.
        fs::remove_dir_all(&final_dir)
            .map_err(|e| format!("remove stale {}: {e}", final_dir.display()))?;
    }
    fs::rename(&extract_tmp, &final_dir).map_err(|e| {
        let _ = fs::remove_dir_all(&extract_tmp);
        format!("atomic rename to {} failed: {e}", final_dir.display())
    })?;

    // 5: record. Only now does the new runtime become "current".
    let record = CurrentRuntime {
        version: manifest.version.clone(),
        dir: format!("cpu-{}", manifest.version),
        python_exe: python_rel,
        installed_at_unix: SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0),
        protocol: crate::process::EXPECTED_PROTOCOL,
    };
    let json = serde_json::to_string_pretty(&record).map_err(|e| e.to_string())?;
    fs::write(paths.root.join("current-runtime.json"), json)
        .map_err(|e| format!("write current-runtime.json: {e}"))?;

    let _ = fs::remove_file(&partial);
    emit_progress(app, "done", manifest.size, manifest.size, 0);
    Ok(())
}

/// Range-resumable download with streaming SHA-256 verification.
async fn download_with_resume(
    app: &AppHandle,
    manifest: &BackendManifest,
    partial: &Path,
) -> Result<(), String> {
    let mut hasher = Sha256::new();
    let mut downloaded: u64 = 0;

    // Re-hash any existing partial bytes so the final digest covers the whole
    // file (blocking read moved off the async runtime).
    if partial.is_file() {
        emit_progress(app, "resume-check", 0, manifest.size, 0);
        let path = partial.to_path_buf();
        let (h, len) = tokio::task::spawn_blocking(move || -> std::io::Result<(Sha256, u64)> {
            let mut h = Sha256::new();
            let mut f = fs::File::open(&path)?;
            let mut buf = vec![0u8; 1024 * 1024];
            let mut total = 0u64;
            loop {
                let n = f.read(&mut buf)?;
                if n == 0 {
                    break;
                }
                h.update(&buf[..n]);
                total += n as u64;
            }
            Ok((h, total))
        })
        .await
        .map_err(|e| format!("resume-check task panicked: {e}"))?
        .map_err(|e| format!("failed to read partial file: {e}"))?;
        hasher = h;
        downloaded = len;
    }

    if downloaded > manifest.size {
        // Corrupt partial (bigger than the target): start over.
        let _ = fs::remove_file(partial);
        hasher = Sha256::new();
        downloaded = 0;
    }

    if downloaded < manifest.size {
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(20))
            // No global timeout: this is a multi-hundred-MB streaming download.
            .build()
            .map_err(|e| e.to_string())?;

        let mut req = client.get(&manifest.url);
        if downloaded > 0 {
            req = req.header(reqwest::header::RANGE, format!("bytes={downloaded}-"));
        }
        let resp = req.send().await.map_err(|e| format!("download request failed: {e}"))?;

        let status = resp.status();
        let mut file = match status.as_u16() {
            206 => fs::OpenOptions::new()
                .append(true)
                .open(partial)
                .map_err(|e| format!("open partial for append: {e}"))?,
            200 => {
                // Server ignored Range (or fresh download): restart from zero.
                if downloaded > 0 {
                    hasher = Sha256::new();
                    downloaded = 0;
                }
                let mut f = fs::OpenOptions::new()
                    .create(true)
                    .write(true)
                    .open(partial)
                    .map_err(|e| format!("create partial: {e}"))?;
                f.set_len(0).and_then(|_| f.seek(SeekFrom::Start(0)).map(|_| ()))
                    .map_err(|e| format!("truncate partial: {e}"))?;
                f
            }
            _ => return Err(format!("download failed: HTTP {status}")),
        };

        let started = Instant::now();
        let resumed_from = downloaded;
        let mut last_emit = Instant::now() - PROGRESS_INTERVAL;
        let mut stream = resp.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|e| format!("download stream error: {e}"))?;
            file.write_all(&chunk)
                .map_err(|e| format!("write partial: {e}"))?;
            hasher.update(&chunk);
            downloaded += chunk.len() as u64;
            if downloaded > manifest.size {
                return Err(format!(
                    "server sent more bytes ({downloaded}) than the manifest size ({}); \
                     aborting (manifest/asset mismatch?)",
                    manifest.size
                ));
            }
            if last_emit.elapsed() >= PROGRESS_INTERVAL {
                last_emit = Instant::now();
                let elapsed = started.elapsed().as_secs_f64().max(0.001);
                let bps = ((downloaded - resumed_from) as f64 / elapsed) as u64;
                emit_progress(app, "download", downloaded, manifest.size, bps);
            }
        }
        file.flush().map_err(|e| format!("flush partial: {e}"))?;
    }

    if downloaded != manifest.size {
        return Err(format!(
            "incomplete download: got {downloaded} of {} bytes (retry to resume)",
            manifest.size
        ));
    }

    emit_progress(app, "verify", downloaded, manifest.size, 0);
    let digest = hex_lower(&hasher.finalize());
    if !digest.eq_ignore_ascii_case(manifest.sha256.trim()) {
        // Corrupt archive: remove so the retry starts clean.
        let _ = fs::remove_file(partial);
        return Err(format!(
            "SHA-256 mismatch: expected {}, got {digest}. The partial file was deleted; retry to re-download.",
            manifest.sha256
        ));
    }
    Ok(())
}

/// Extracts a zip archive with zip-slip protection.
fn extract_zip(archive: &Path, dest: &Path) -> Result<(), String> {
    let file = fs::File::open(archive).map_err(|e| format!("open archive: {e}"))?;
    let mut zip = zip::ZipArchive::new(file).map_err(|e| format!("invalid zip archive: {e}"))?;
    fs::create_dir_all(dest).map_err(|e| format!("create {}: {e}", dest.display()))?;
    for i in 0..zip.len() {
        let mut entry = zip.by_index(i).map_err(|e| format!("zip entry {i}: {e}"))?;
        let Some(rel) = entry.enclosed_name() else {
            return Err(format!("zip entry {i} has an unsafe path (zip-slip)"));
        };
        let out_path = dest.join(rel);
        if entry.is_dir() {
            fs::create_dir_all(&out_path).map_err(|e| format!("mkdir {}: {e}", out_path.display()))?;
        } else {
            if let Some(parent) = out_path.parent() {
                fs::create_dir_all(parent).map_err(|e| format!("mkdir {}: {e}", parent.display()))?;
            }
            let mut out = fs::File::create(&out_path)
                .map_err(|e| format!("create {}: {e}", out_path.display()))?;
            std::io::copy(&mut entry, &mut out)
                .map_err(|e| format!("extract {}: {e}", out_path.display()))?;
        }
    }
    Ok(())
}

/// Finds python.exe inside a freshly extracted runtime tree. Honors the
/// manifest's `python_exe` first, then the layouts python-build-standalone
/// archives are known to use.
fn resolve_python_exe(dir: &Path, manifest_rel: Option<&str>) -> Result<String, String> {
    let mut candidates: Vec<String> = Vec::new();
    if let Some(rel) = manifest_rel {
        candidates.push(rel.to_string());
    }
    candidates.extend(
        ["python.exe", "python/python.exe", "install/python.exe", "python/install/python.exe"]
            .iter()
            .map(|s| s.to_string()),
    );
    for rel in &candidates {
        if dir.join(rel).is_file() {
            return Ok(rel.clone());
        }
    }
    Err(format!(
        "python.exe not found in the extracted runtime (tried: {})",
        candidates.join(", ")
    ))
}

fn hex_lower(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
