# 构建UCM-Deployer.exe
# 用法: powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv")) {
    Write-Host "[1/4] 创建虚拟环境..."
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "创建 venv 失败，请确认已安装 Python 3.9+" }
}

Write-Host "[2/4] 安装依赖..."
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\pip.exe install -q -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }

Write-Host "[3/4] 运行测试..."
$env:PYTHONIOENCODING = "utf-8"
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python.exe -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw "测试未通过，中止打包" }

Write-Host "[4/4] PyInstaller 打包..."
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean ucm_deployer.spec
if ($LASTEXITCODE -ne 0) { throw "打包失败" }

$exe = "dist\UCM-Deployer.exe"
if (-not (Test-Path $exe)) { throw "未找到 $exe" }
$size = "{0:N1}" -f ((Get-Item $exe).Length / 1MB)
Write-Host ""
Write-Host "构建完成: $exe ($size MB)"
Write-Host "自检验证: .\dist\UCM-Deployer.exe --selftest --out selftest-report.txt"
