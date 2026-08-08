<#
.SYNOPSIS
  Build the relocatable CPU backend runtime archive for the Transcribe desktop app.

.DESCRIPTION
  Produces: <OutputDir>/transcribe-backend-<BackendVersion>-win64.zip
            <OutputDir>/transcribe-backend-<BackendVersion>-win64.zip.sha256
            <OutputDir>/backend-manifest.json  ({version, url, sha256, size})

  Steps:
    1. Download the pinned python-build-standalone CPython (x86_64-pc-windows-msvc,
       install_only[_stripped]) and verify its SHA-256 against python-pin.json.
    2. Extract to a work dir (<root>/python.exe + Lib/site-packages layout).
    3. pip install --no-deps --require-hashes -r requirements-windows.lock using the
       standalone python itself (NO cross-install: this script MUST run on Windows x64).
    4. Prune tests/ and __pycache__/ from site-packages (dist-info RECORD is kept).
    5. Guard: FAIL if any nvidia-* / *cuda* package is present (CPU-only contract).
    6. Relocation smoke test: copy the runtime to a different path and import
       torch, whisper, pyannote.audio, fastapi, pywhispercpp, silero_vad.
    7. Zip with deterministic entry ordering + fixed timestamps, emit SHA-256 and
       backend-manifest.json.

.NOTES
  *** AUTHORED ON WSL2 LINUX — UNVERIFIED (未検証). ***
  This script has never been executed; it targets Windows PowerShell 5.1+ on a
  Windows x64 host (e.g. the windows-2022 GitHub runner). Expect to iterate on CI.

  Requires: tar.exe on PATH (ships with Windows 10 1803+ / windows-2022 runners).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\backend\build.ps1 `
    -BackendVersion cpu-2026.07.1 `
    -ReleaseBaseUrl https://github.com/OWNER/transcribe/releases/download/backend-cpu-2026.07.1
#>
[CmdletBinding()]
param(
    # Version string recorded in the manifest and archive name, e.g. "cpu-2026.07.1".
    [Parameter(Mandatory = $true)]
    [string]$BackendVersion,

    # Base URL under which the zip will be published (GitHub Releases download URL,
    # WITHOUT trailing slash). Recorded verbatim in backend-manifest.json.
    [Parameter(Mandatory = $true)]
    [string]$ReleaseBaseUrl,

    # Pin file for python-build-standalone (version/url/sha256).
    [string]$PinFile = (Join-Path $PSScriptRoot 'python-pin.json'),

    # Hash-locked requirements produced by make-lock.ps1 (on Windows).
    [string]$LockFile = (Join-Path $PSScriptRoot 'requirements-windows.lock'),

    # Where build outputs are written.
    [string]$OutputDir = (Join-Path $PSScriptRoot 'dist'),

    # Scratch dir (deleted and recreated).
    [string]$WorkDir = (Join-Path $PSScriptRoot 'work'),

    # Skip the relocation smoke test (NOT recommended; for local debugging only).
    [switch]$SkipSmokeTest
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# PS 5.1 defaults to TLS 1.0/1.1 on some hosts; force TLS 1.2 for GitHub.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Write-Step([string]$Message) {
    Write-Host "==> $Message"
}

function Get-Sha256([string]$Path) {
    (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

# ---------------------------------------------------------------- preconditions
if ($env:OS -ne 'Windows_NT') {
    throw "build.ps1 must run on Windows x64 (it executes the standalone python.exe). Current OS: $env:OS"
}

if (-not (Test-Path -LiteralPath $PinFile)) { throw "Pin file not found: $PinFile" }
if (-not (Test-Path -LiteralPath $LockFile)) { throw "Lock file not found: $LockFile" }

$lockHead = Get-Content -LiteralPath $LockFile -TotalCount 40
if ($lockHead -match 'LOCKFILE-PLACEHOLDER') {
    throw ("requirements-windows.lock is still the placeholder. Run packaging\backend\make-lock.ps1 " +
           "on a Windows x64 host with Python 3.12 to generate real pins+hashes first.")
}

$pin = Get-Content -LiteralPath $PinFile -Raw | ConvertFrom-Json
if ($pin.sha256 -like 'REPLACE_ME*') {
    throw "python-pin.json sha256 is a placeholder. Pin a real python-build-standalone release first (see file header)."
}

Write-Step "Backend version: $BackendVersion"
Write-Step "Python runtime : cpython $($pin.python_version) ($($pin.release_tag)) x86_64-pc-windows-msvc"

# ---------------------------------------------------------------- workspace
if (Test-Path -LiteralPath $WorkDir) { Remove-Item -LiteralPath $WorkDir -Recurse -Force }
New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$archivePath = Join-Path $WorkDir 'python-standalone.tar.gz'
$extractDir  = Join-Path $WorkDir 'extract'
$runtimeDir  = Join-Path $WorkDir 'runtime'   # final relocatable root: runtime\python.exe

# ---------------------------------------------------------------- 1. download + verify
Write-Step "Downloading $($pin.url)"
Invoke-WebRequest -Uri $pin.url -OutFile $archivePath -UseBasicParsing

$actual = Get-Sha256 $archivePath
if ($actual -ne $pin.sha256.ToLowerInvariant()) {
    throw "SHA-256 mismatch for python-build-standalone archive.`n expected: $($pin.sha256)`n actual  : $actual"
}
Write-Step "SHA-256 OK: $actual"

# ---------------------------------------------------------------- 2. extract
Write-Step "Extracting runtime"
New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
# tar.exe (bsdtar) ships with Windows 10 1803+ and the windows-2022 runner.
& tar -xzf $archivePath -C $extractDir
if ($LASTEXITCODE -ne 0) { throw "tar extraction failed (exit $LASTEXITCODE)" }

# install_only archives extract to a top-level 'python' directory.
$pyRoot = Join-Path $extractDir 'python'
if (-not (Test-Path -LiteralPath (Join-Path $pyRoot 'python.exe'))) {
    # Fallback: single top-level dir with python.exe inside.
    $candidates = @(Get-ChildItem -LiteralPath $extractDir -Directory)
    if ($candidates.Count -eq 1 -and (Test-Path (Join-Path $candidates[0].FullName 'python.exe'))) {
        $pyRoot = $candidates[0].FullName
    } else {
        throw "Could not locate python.exe in extracted archive under $extractDir"
    }
}
Move-Item -LiteralPath $pyRoot -Destination $runtimeDir
$python = Join-Path $runtimeDir 'python.exe'
Write-Step "Runtime at $runtimeDir"

# ---------------------------------------------------------------- 3. pip install (hash-locked)
# install_only builds are documented to include pip; bootstrap via ensurepip as a fallback.
& $python -m pip --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Step "pip missing; bootstrapping via ensurepip"
    & $python -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw "ensurepip failed (exit $LASTEXITCODE)" }
}

Write-Step "Installing locked requirements into Lib\site-packages (--no-deps --require-hashes)"
# Index URLs are emitted inside the lock file by make-lock.ps1 (PyPI + the CPU-only
# torch index https://download.pytorch.org/whl/cpu), so none are passed here.
& $python -m pip install --no-deps --require-hashes --no-warn-script-location --disable-pip-version-check -r $LockFile
if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit $LASTEXITCODE)" }

$sitePackages = Join-Path $runtimeDir 'Lib\site-packages'
if (-not (Test-Path -LiteralPath $sitePackages)) {
    throw "Expected site-packages not found at $sitePackages (unexpected runtime layout)"
}

# ---------------------------------------------------------------- 4. CUDA / NVIDIA guard
Write-Step "Checking for CUDA/NVIDIA contamination (CPU-only contract)"
$badDirs = @(Get-ChildItem -LiteralPath $sitePackages -Directory | Where-Object {
    $_.Name -match '^nvidia([-_.]|$)' -or $_.Name -match 'cuda'
})
$badDists = @(Get-ChildItem -LiteralPath $sitePackages -Directory -Filter '*.dist-info' | Where-Object {
    $_.Name -match '^nvidia[-_]' -or $_.Name -match 'cuda'
})
$bad = @($badDirs) + @($badDists)
if ($bad.Count -gt 0) {
    $names = ($bad | ForEach-Object { $_.Name }) -join ', '
    throw "CUDA/NVIDIA packages found in site-packages: $names. The desktop backend must be CPU-only; fix requirements-windows.lock (torch must come from https://download.pytorch.org/whl/cpu)."
}
# Belt-and-braces: torch itself must report no CUDA build.
& $python -c "import torch,sys; sys.exit(1 if (torch.version.cuda is not None) else 0)"
if ($LASTEXITCODE -ne 0) { throw "torch reports a CUDA build (torch.version.cuda is set). CPU-only contract violated." }

# ---------------------------------------------------------------- 5. prune
Write-Step "Pruning site-packages (tests/, __pycache__/)"
# NOTE: only directories named exactly 'tests' are removed. 'test'/'testing' are
# intentionally kept (torch.testing etc. are imported at runtime by dependents).
# dist-info (including RECORD) is intentionally kept.
Get-ChildItem -LiteralPath $sitePackages -Recurse -Directory -Filter '__pycache__' |
    Sort-Object { $_.FullName.Length } -Descending |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
Get-ChildItem -LiteralPath $sitePackages -Recurse -Directory -Filter 'tests' |
    Sort-Object { $_.FullName.Length } -Descending |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

# ---------------------------------------------------------------- 6. relocation smoke test
if (-not $SkipSmokeTest) {
    Write-Step "Relocation smoke test (copy runtime to a different path, import heavy deps)"
    $relocDir = Join-Path $WorkDir 'reloc-smoke\moved-runtime'
    New-Item -ItemType Directory -Path (Split-Path $relocDir) -Force | Out-Null
    Copy-Item -LiteralPath $runtimeDir -Destination $relocDir -Recurse
    $relocPython = Join-Path $relocDir 'python.exe'
    $smoke = @(
        'import torch',
        'import whisper',
        'import pyannote.audio',
        'import fastapi',
        'import pywhispercpp.model',
        'import silero_vad',
        'print("RELOCATION_SMOKE_OK", torch.__version__)'
    ) -join '; '
    & $relocPython -c $smoke
    if ($LASTEXITCODE -ne 0) {
        throw "Relocation smoke test FAILED. The runtime is not relocatable as built (see task file risk #3: fallback is PyInstaller onedir)."
    }
    Remove-Item -LiteralPath (Join-Path $WorkDir 'reloc-smoke') -Recurse -Force
} else {
    Write-Warning "Relocation smoke test SKIPPED (-SkipSmokeTest). Do not release this archive."
}

# ---------------------------------------------------------------- 7. deterministic zip + manifest
$zipName = "transcribe-backend-$BackendVersion-win64.zip"
$zipPath = Join-Path $OutputDir $zipName

Write-Step "Creating deterministic zip: $zipPath"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }

# Deterministic = stable ordinal entry order + fixed timestamps. (Byte-identical
# output across different .NET versions is NOT guaranteed — the deflate encoder
# may differ — but a rebuild on the same runner image is stable.)
$rootFull = (Get-Item -LiteralPath $runtimeDir).FullName
$files = @(Get-ChildItem -LiteralPath $runtimeDir -Recurse -File | ForEach-Object { $_.FullName })
[Array]::Sort($files, [System.StringComparer]::Ordinal)
$epoch = New-Object System.DateTimeOffset(1980, 1, 1, 0, 0, 0, ([TimeSpan]::Zero))

$zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($f in $files) {
        $rel = $f.Substring($rootFull.Length).TrimStart('\', '/') -replace '\\', '/'
        # Archive layout: everything under a top-level 'runtime/' folder so the
        # extracted tree is runtimes/<ver>/runtime/python.exe -- keep in sync with
        # the Rust downloader (src-tauri, Phase B).
        $entry = $zip.CreateEntry("runtime/$rel", [System.IO.Compression.CompressionLevel]::Optimal)
        $entry.LastWriteTime = $epoch
        $inStream = [System.IO.File]::OpenRead($f)
        try {
            $outStream = $entry.Open()
            try { $inStream.CopyTo($outStream) } finally { $outStream.Dispose() }
        } finally { $inStream.Dispose() }
    }
} finally {
    $zip.Dispose()
}

$zipSha = Get-Sha256 $zipPath
$zipSize = (Get-Item -LiteralPath $zipPath).Length
# Text sidecar in `sha256sum` format (LF, no BOM) for easy verification.
[System.IO.File]::WriteAllText("$zipPath.sha256", "$zipSha  $zipName`n")

$manifest = [ordered]@{
    version = $BackendVersion
    url     = "$ReleaseBaseUrl/$zipName"
    sha256  = $zipSha
    size    = $zipSize
}
$manifestPath = Join-Path $OutputDir 'backend-manifest.json'
$manifestJson = ($manifest | ConvertTo-Json)
[System.IO.File]::WriteAllText($manifestPath, $manifestJson + "`n")

Write-Step "Done."
Write-Host "  zip      : $zipPath"
Write-Host "  sha256   : $zipSha"
Write-Host "  size     : $zipSize bytes"
Write-Host "  manifest : $manifestPath"
