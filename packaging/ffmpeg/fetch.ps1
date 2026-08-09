<#
.SYNOPSIS
  Fetch, verify and stage the pinned BtbN LGPL static ffmpeg.exe for bundling as a
  Tauri resource.

.DESCRIPTION
  Reads packaging/ffmpeg/pin.json (url + sha256 + version), then:
    1. Downloads the pinned BtbN win64 lgpl zip and verifies its SHA-256.
    2. Extracts and locates bin\ffmpeg.exe.
    3. Runs `ffmpeg -version` (must exit 0).
    4. License checks — build FAILS if any of these trip:
         - `ffmpeg -L` does not contain "Lesser General Public License"
         - `-version`/`-buildconf` configuration contains --enable-gpl,
           --enable-nonfree, --enable-libx264 or --enable-libx265
    5. Stages into <OutputDir>:
         ffmpeg.exe
         LICENSE.txt   (copied from the BtbN archive)
         SOURCE.txt    (generated from SOURCE.txt.template with exact provenance)

  <OutputDir> is what the Tauri build bundles (see .github/workflows/desktop-windows.yml
  and src-tauri/tauri.conf.json bundle.resources — owned by Phase B).

.NOTES
  *** AUTHORED ON WSL2 LINUX — UNVERIFIED (未検証). Never executed. ***
  Targets Windows PowerShell 5.1+ on Windows x64 (ffmpeg.exe is executed for the
  checks, so this cannot run on Linux).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\ffmpeg\fetch.ps1
#>
[CmdletBinding()]
param(
    [string]$PinFile,
    [string]$Template,
    [string]$OutputDir,
    [string]$WorkDir
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$scriptDir = Split-Path -Parent $PSCommandPath
if ([string]::IsNullOrWhiteSpace($PinFile)) { $PinFile = Join-Path $scriptDir 'pin.json' }
if ([string]::IsNullOrWhiteSpace($Template)) { $Template = Join-Path $scriptDir 'SOURCE.txt.template' }
if ([string]::IsNullOrWhiteSpace($OutputDir)) { $OutputDir = Join-Path $scriptDir 'dist' }
if ([string]::IsNullOrWhiteSpace($WorkDir)) { $WorkDir = Join-Path $scriptDir 'work' }
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Write-Step([string]$Message) { Write-Host "==> $Message" }

if ($env:OS -ne 'Windows_NT') {
    throw "fetch.ps1 must run on Windows (it executes ffmpeg.exe for license verification)."
}
if (-not (Test-Path -LiteralPath $PinFile)) { throw "Pin file not found: $PinFile" }
if (-not (Test-Path -LiteralPath $Template)) { throw "SOURCE.txt template not found: $Template" }

$pin = Get-Content -LiteralPath $PinFile -Raw | ConvertFrom-Json
if ($pin.sha256 -like 'REPLACE_ME*' -or $pin.url -like '*REPLACE_ME*') {
    throw "packaging/ffmpeg/pin.json still contains placeholders. Pin a real dated BtbN win64-lgpl release first (see the _comment field)."
}

if (Test-Path -LiteralPath $WorkDir) { Remove-Item -LiteralPath $WorkDir -Recurse -Force }
New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

# ---------------------------------------------------------------- download + verify
$zipPath = Join-Path $WorkDir 'ffmpeg-btbn.zip'
Write-Step "Downloading $($pin.url)"
Invoke-WebRequest -Uri $pin.url -OutFile $zipPath -UseBasicParsing

$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
if ($actual -ne $pin.sha256.ToLowerInvariant()) {
    throw "SHA-256 mismatch for ffmpeg archive.`n expected: $($pin.sha256)`n actual  : $actual"
}
Write-Step "SHA-256 OK: $actual"

# ---------------------------------------------------------------- extract + locate
$extractDir = Join-Path $WorkDir 'extract'
Expand-Archive -LiteralPath $zipPath -DestinationPath $extractDir -Force

$ffmpegExe = Get-ChildItem -LiteralPath $extractDir -Recurse -File -Filter 'ffmpeg.exe' | Select-Object -First 1
if ($null -eq $ffmpegExe) { throw "ffmpeg.exe not found inside the archive." }
$ffmpeg = $ffmpegExe.FullName
Write-Step "Found $ffmpeg"

$licenseFile = Get-ChildItem -LiteralPath $extractDir -Recurse -File |
    Where-Object { $_.Name -match '^LICENSE(\.txt)?$' } | Select-Object -First 1
if ($null -eq $licenseFile) { throw "LICENSE file not found inside the archive (required for LGPL compliance)." }

# ---------------------------------------------------------------- functional check
Write-Step "Running ffmpeg -version"
$versionOut = (& $ffmpeg -hide_banner -version 2>&1) | Out-String
if ($LASTEXITCODE -ne 0) { throw "ffmpeg -version failed (exit $LASTEXITCODE):`n$versionOut" }
Write-Host $versionOut

# ---------------------------------------------------------------- license checks
Write-Step "Verifying LGPL licensing (fail on GPL/nonfree)"

# 1. `ffmpeg -L` prints the effective license. LGPL builds print the GNU LESSER
#    General Public License; GPL builds print the GNU General Public License;
#    nonfree builds mention nonfree/unredistributable.
$licenseOut = (& $ffmpeg -hide_banner -L 2>&1) | Out-String
if ($LASTEXITCODE -ne 0) { throw "ffmpeg -L failed (exit $LASTEXITCODE)" }
if ($licenseOut -notmatch 'Lesser General Public License') {
    throw "ffmpeg -L does not report the GNU Lesser General Public License. This is NOT an LGPL build.`n$licenseOut"
}
if ($licenseOut -match 'nonfree|incompatible with the GNU General Public License') {
    throw "ffmpeg -L reports a nonfree/unredistributable build.`n$licenseOut"
}

# 2. Inspect configuration flags from -buildconf (plus the -version banner).
$buildconfOut = (& $ffmpeg -hide_banner -buildconf 2>&1) | Out-String
if ($LASTEXITCODE -ne 0) { throw "ffmpeg -buildconf failed (exit $LASTEXITCODE)" }
$combined = $buildconfOut + "`n" + $versionOut
$forbidden = @('--enable-gpl', '--enable-nonfree', '--enable-libx264', '--enable-libx265')
foreach ($flag in $forbidden) {
    if ($combined -match [regex]::Escape($flag)) {
        throw "Forbidden configure flag '$flag' present in ffmpeg build configuration - not redistributable under our LGPL policy.`n$buildconfOut"
    }
}
Write-Step "License checks passed (LGPL, no gpl/nonfree/x264/x265 flags)."

# ---------------------------------------------------------------- stage outputs
Write-Step "Staging into $OutputDir"
Copy-Item -LiteralPath $ffmpeg -Destination (Join-Path $OutputDir 'ffmpeg.exe') -Force
Copy-Item -LiteralPath $licenseFile.FullName -Destination (Join-Path $OutputDir 'LICENSE.txt') -Force

# Generate SOURCE.txt from the template with exact provenance.
$sourceText = Get-Content -LiteralPath $Template -Raw
$sourceText = $sourceText.Replace('{{FFMPEG_VERSION}}', [string]$pin.version)
$sourceText = $sourceText.Replace('{{BTBN_RELEASE_TAG}}', [string]$pin.release_tag)
$sourceText = $sourceText.Replace('{{BUILD_URL}}', [string]$pin.url)
$sourceText = $sourceText.Replace('{{BUILD_SHA256}}', $actual)
$sourceText = $sourceText.Replace('{{SOURCE_URL}}', [string]$pin.source_url)
$sourceText = $sourceText.Replace('{{FETCH_DATE}}', (Get-Date -Format 'yyyy-MM-dd'))
[System.IO.File]::WriteAllText((Join-Path $OutputDir 'SOURCE.txt'), $sourceText)

Write-Step "Done. Staged files:"
Get-ChildItem -LiteralPath $OutputDir | ForEach-Object { Write-Host "  $($_.Name)  ($($_.Length) bytes)" }
