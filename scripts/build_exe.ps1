# Build UCM-Deployer.exe
# Usage: powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv")) {
    Write-Host "[1/4] Creating venv..."
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create venv (Python 3.9+ required)" }
} else {
    Write-Host "[1/4] venv exists"
}

Write-Host "[2/4] Installing dependencies..."
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\pip.exe install -q -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Online install failed (network/proxy). Checking local deps..."
    .venv\Scripts\python.exe -c "import paramiko, PySide6, pytest, PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Dependencies incomplete and cannot install online; check network/proxy"
    }
    Write-Warning "Local deps complete, continuing"
}

Write-Host "[3/4] Running tests..."
$env:PYTHONIOENCODING = "utf-8"
$env:QT_QPA_PLATFORM = "offscreen"
# Note: tests exit early via TerminateProcess (avoids a Windows Qt/paramiko
# teardown crash), so the pytest summary line is truncated by design;
# the exit code matches pytest's.
.venv\Scripts\python.exe -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw "Tests FAILED, aborting build" }

Write-Host "[4/4] PyInstaller packaging..."
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean ucm_deployer.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

$exe = "dist\UCM-Deployer.exe"
if (-not (Test-Path $exe)) { throw "Not found: $exe" }
$size = "{0:N1}" -f ((Get-Item $exe).Length / 1MB)
Write-Host ""
Write-Host "Build OK: $exe ($size MB)"
Write-Host "Self check: .\dist\UCM-Deployer.exe --smoke"
