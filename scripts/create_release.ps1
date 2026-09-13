# 一键发布 GitHub Release（无 gh CLI 时自动走 REST API）
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\create_release.ps1              # 标签取 version.py 的 v{version}
#   powershell -ExecutionPolicy Bypass -File scripts\create_release.ps1 -Tag v0.2.0  # 指定标签
# 前置: 1) 已运行 build_exe.ps1 产出 dist\UCM-Deployer.exe
#       2) git 凭据管理器中有 github.com 的令牌（或安装 gh CLI 后可改用 gh）
# 说明: 本机系统代理不可用时自动绕过代理直连 api.github.com
param(
    [string]$Tag = "",
    [string]$Title = "",
    [string]$NotesPath = "scripts\release_notes_template.md"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# ---------- 1. 版本号与标签
$version = (Select-String -Path "ucm_deployer\version.py" -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
if (-not $Tag) { $Tag = "v$version" }
if (-not $Title) { $Title = "UCM Deployer $Tag" }
Write-Host "发布标签: $Tag ($Title)"

# ---------- 2. 产物检查
$exe = "dist\UCM-Deployer.exe"
if (-not (Test-Path $exe)) { throw "未找到 $exe，请先运行 scripts\build_exe.ps1" }
$sizeMB = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "产物: $exe ($sizeMB MB)"

# ---------- 3. 凭据（不回显）
$cred = "url=https://github.com`n`n" | git credential fill
$passLine = ($cred | Select-String "^password=").Line
if (-not $passLine) { throw "git 凭据管理器中未找到 github.com 凭据（HTTPS）。请先对 HTTPS 远端执行一次 git pull 输入令牌，或安装 gh CLI。" }
$pass = $passLine.Substring(9)
$repo = "student-jhz/UCM-Deployer"
$api = "https://api.github.com/repos/$repo"

function Invoke-GhApi {
    param([string]$Method, [string]$Url, [string]$BodyFile = "", [string]$ContentType = "application/json; charset=utf-8")
    $args = @("--noproxy", "*", "-s", "--connect-timeout", "30", "--max-time", "900",
              "-X", $Method,
              "-H", "Authorization: Bearer $pass",
              "-H", "User-Agent: ucm-release",
              "-H", "Accept: application/vnd.github+json")
    if ($BodyFile) {
        $args += @("-H", "Content-Type: $ContentType", "-d", "@$BodyFile")
    }
    $args += $Url
    $raw = & curl.exe @args
    if ($LASTEXITCODE -ne 0) { throw "GitHub API 调用失败($Method $Url): curl 退出码 $LASTEXITCODE" }
    return ($raw | ConvertFrom-Json)
}

# ---------- 4. 校验令牌
$me = Invoke-GhApi "GET" "https://api.github.com/user"
Write-Host "令牌身份: $($me.login)"

# ---------- 5. 标签（不存在则创建并推送）
$existingTag = git tag -l $Tag
if (-not $existingTag) {
    git tag -a $Tag -m "$Title"
    if ($LASTEXITCODE -ne 0) { throw "创建标签失败" }
    git push origin $Tag
    if ($LASTEXITCODE -ne 0) { throw "推送标签失败" }
    Write-Host "已创建并推送标签 $Tag"
} else {
    Write-Host "标签 $Tag 已存在"
}

# ---------- 6. Release（已存在则复用并更新说明）
$rel = $null
try {
    $rel = Invoke-GhApi "GET" "$api/releases/tags/$Tag"
} catch { $rel = $null }

if ($rel -and $rel.id) {
    Write-Host "Release 已存在(id=$($rel.id))，更新说明"
    $bodyText = (Get-Content $NotesPath -Raw -Encoding UTF8).Replace("{VERSION}", $version)
    $tmp = Join-Path $env:TEMP "gh_release_body.json"
    $json = @{ body = $bodyText } | ConvertTo-Json
    [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding $false))
    $null = Invoke-GhApi "PATCH" "$api/releases/$($rel.id)" $tmp
    $releaseId = $rel.id
} else {
    $bodyText = (Get-Content $NotesPath -Raw -Encoding UTF8).Replace("{VERSION}", $version)
    $tmp = Join-Path $env:TEMP "gh_release_body.json"
    $json = @{ tag_name = $Tag; name = $Title; body = $bodyText } | ConvertTo-Json
    [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding $false))
    $rel = Invoke-GhApi "POST" "$api/releases" $tmp
    if (-not $rel.id) { throw "创建 Release 失败: $($rel | ConvertTo-Json -Depth 3)" }
    $releaseId = $rel.id
    Write-Host "Release 已创建 id=$releaseId"
}

# ---------- 7. 上传资产（同名资产先删除；网络抖动自动重试）
foreach ($asset in @($rel.assets)) {
    if ($asset.name -eq "UCM-Deployer.exe") {
        $null = Invoke-GhApi "DELETE" "$api/releases/assets/$($asset.id)"
        Write-Host "已删除旧资产，重新上传"
    }
}
Write-Host "上传资产 $exe ($sizeMB MB)..."
$expectedSize = (Get-Item $exe).Length
$uploaded = $null
for ($attempt = 1; $attempt -le 5 -and -not $uploaded; $attempt++) {
    try {
        $uploaded = Invoke-GhApi "POST" `
            "https://uploads.github.com/repos/$repo/releases/$releaseId/assets?name=UCM-Deployer.exe" `
            "$exe" "application/octet-stream"
        if ($uploaded -and $uploaded.size -ne $expectedSize) {
            # 防静默截断：服务端大小与本地不一致时视为失败，删除后重传
            $gotMB = [math]::Round($uploaded.size / 1MB, 1)
            Write-Warning "上传不完整(服务端 $gotMB MB / 本地 $sizeMB MB)，删除后重试"
            $null = Invoke-GhApi "DELETE" "$api/releases/assets/$($uploaded.id)"
            $uploaded = $null
            throw "size-mismatch"
        }
    } catch {
        $uploaded = $null
        Write-Warning "上传失败(第 $attempt/5 次): $($_.Exception.Message)"
        if ($attempt -lt 5) {
            $wait = 5 * $attempt
            Write-Host "等待 ${wait}s 后重试..."
            Start-Sleep -Seconds $wait
        }
    }
}
if (-not $uploaded) {
    throw "上传连续失败。Release 已创建，可重跑本脚本自动重传资产（不会重复创建 Release）"
}
Write-Host "上传完成 state=$($uploaded.state) size=$([math]::Round($uploaded.size/1MB,1))MB(已校验)"

Write-Host ""
Write-Host "发布完成: https://github.com/$repo/releases/tag/$Tag"
Write-Host "下载地址: $($uploaded.browser_download_url)"
