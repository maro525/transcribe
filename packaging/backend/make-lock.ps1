<#
.SYNOPSIS
  Generate (or check) packaging/backend/requirements-windows.lock from requirements.txt.

.DESCRIPTION
  Uses pip-tools (pip-compile) with --generate-hashes so that build.ps1 can install
  with `pip install --no-deps --require-hashes`. Resolution happens against:
    - primary index : https://pypi.org/simple
    - extra index   : https://download.pytorch.org/whl/cpu   (CPU-only torch/torchaudio)

  MUST run on a Windows x64 host with CPython 3.12 (environment markers and wheel
  hashes are resolved for the target platform: cp312 / win_amd64). Do NOT run this
  on Linux/WSL — the resulting lock would pin the wrong wheels.

  -Check recompiles into a temp file and fails (exit 1) if the committed lock is
  out of date or still the placeholder. Used by CI.

.NOTES
  *** AUTHORED ON WSL2 LINUX — UNVERIFIED (未検証). Never executed. ***

  Caveat (verify on first real run): `torch==2.2.2` may resolve to the `2.2.2+cpu`
  local-version wheel from the CPU index or to the plain `2.2.2` win_amd64 wheel
  from PyPI (which is also CPU-only on Windows). Either satisfies the CPU-only
  contract; build.ps1 independently rejects any nvidia-*/CUDA package.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\backend\make-lock.ps1
  powershell -ExecutionPolicy Bypass -File packaging\backend\make-lock.ps1 -Check
#>
[CmdletBinding()]
param(
    # Input requirements (repo root requirements.txt by default).
    [string]$Requirements,

    # Output lock file.
    [string]$LockFile,

    # Python 3.12 interpreter to run pip-compile with (must be win_amd64 CPython 3.12).
    [string]$Python = 'python',

    # Verify mode: fail if the committed lock differs from a fresh compile.
    [switch]$Check
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$PIP_TOOLS_VERSION = '7.4.1'   # pinned; bump deliberately
$TORCH_CPU_INDEX = 'https://download.pytorch.org/whl/cpu'
$scriptDir = Split-Path -Parent $PSCommandPath
if ([string]::IsNullOrWhiteSpace($Requirements)) { $Requirements = Join-Path $scriptDir '..\..\requirements.txt' }
if ([string]::IsNullOrWhiteSpace($LockFile)) { $LockFile = Join-Path $scriptDir 'requirements-windows.lock' }

if ($env:OS -ne 'Windows_NT') {
    throw "make-lock.ps1 must run on Windows x64 (lock is platform-specific: cp312/win_amd64). Current OS: $env:OS"
}
if (-not (Test-Path -LiteralPath $Requirements)) { throw "requirements.txt not found: $Requirements" }

# ---- interpreter sanity: CPython 3.12, 64-bit Windows
& $Python -c "import sys,platform; ok = sys.version_info[:2]==(3,12) and platform.machine().lower() in ('amd64','x86_64') and sys.platform=='win32'; sys.exit(0 if ok else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Interpreter '$Python' is not CPython 3.12 win_amd64. The lock must be resolved on the target platform."
}

# ---- ensure a pip-tools-compatible resolver toolchain
# pip-tools 7.4.1 imports an internal pip symbol removed by pip 25, so pin pip
# below that breaking change before installing/running the locked pip-tools.
& $Python -m pip install --disable-pip-version-check --quiet 'pip<25' "pip-tools==$PIP_TOOLS_VERSION"
if ($LASTEXITCODE -ne 0) { throw "Failed to install pip<25 and pip-tools==$PIP_TOOLS_VERSION" }

function Invoke-PipCompile([string]$OutFile) {
    # --no-strip-extras keeps uvicorn[standard] extras resolvable at install time.
    # --allow-unsafe pins setuptools/pip if they end up in the graph (required for
    #   --require-hashes installs to be complete).
    # Index URLs are emitted into the lock header so build.ps1's plain
    #   `pip install -r` uses the same indexes.
    & $Python -m piptools compile `
        --generate-hashes `
        --allow-unsafe `
        --no-strip-extras `
        --index-url 'https://pypi.org/simple' `
        --extra-index-url $TORCH_CPU_INDEX `
        --output-file $OutFile `
        $Requirements
    if ($LASTEXITCODE -ne 0) { throw "pip-compile failed (exit $LASTEXITCODE)" }
}

function Get-NormalizedLines([string]$Path) {
    # pip-compile records its output path in the command header. That path is
    # intentionally different for a committed lock and the -Check temp file;
    # compare the resolved dependency content rather than that volatile line.
    (Get-Content -LiteralPath $Path) |
        Where-Object { $_ -notmatch '^#    pip-compile ' } |
        ForEach-Object { $_.TrimEnd() }
}

if ($Check) {
    if (-not (Test-Path -LiteralPath $LockFile)) {
        Write-Error "Lock file missing: $LockFile"
        exit 1
    }
    $head = Get-Content -LiteralPath $LockFile -TotalCount 40
    if ($head -match 'LOCKFILE-PLACEHOLDER') {
        Write-Error ("requirements-windows.lock is still the placeholder - it must be generated on Windows " +
                     "(run make-lock.ps1) and committed before building/releasing.")
        exit 1
    }
    $tmp = Join-Path $env:TEMP ("requirements-windows.lock.check-" + [guid]::NewGuid().ToString('N'))
    try {
        # pip-compile treats an existing output file as the baseline for a
        # non-upgrade compile. Seed the temporary output from the committed
        # lock so -Check validates the declared input graph without silently
        # adopting a newly published transitive release from an index.
        Copy-Item -LiteralPath $LockFile -Destination $tmp -Force
        Invoke-PipCompile $tmp
        $a = Get-NormalizedLines $LockFile
        $b = Get-NormalizedLines $tmp
        $diff = Compare-Object -ReferenceObject $a -DifferenceObject $b
        if ($null -ne $diff) {
            Write-Host "Lock file is OUT OF DATE with requirements.txt. Diff (<= committed, => fresh):"
            $diff | ForEach-Object { Write-Host ("  {0} {1}" -f $_.SideIndicator, $_.InputObject) }
            Write-Error "Regenerate with: powershell -File packaging\backend\make-lock.ps1 (on Windows) and commit."
            exit 1
        }
        Write-Host "Lock file is up to date."
    } finally {
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force }
    }
} else {
    Write-Host "Compiling $Requirements -> $LockFile (hashes, CPU torch index)"
    Invoke-PipCompile $LockFile
    Write-Host "Done. Review the diff and commit packaging/backend/requirements-windows.lock."
    Write-Host "Reminder: build.ps1 will fail if any nvidia-*/CUDA package is pinned."
}
