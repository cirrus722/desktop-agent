param(
    [switch]$SkipPortableZip
)

$ErrorActionPreference = "Stop"
$project = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$release = Join-Path $project "build\release"
$payload = Join-Path $release "payload"
$appRoot = Join-Path $payload "app"
$sfxRoot = Join-Path $release "sfx"
$runtimeSource = Join-Path $project "build\portable\DesktopAgent\runtime"
$sevenZip = "C:\Program Files\7-Zip\7z.exe"
$sevenZipSfx = "C:\Program Files\7-Zip\7z.sfx"

if (-not (Test-Path -LiteralPath $sevenZip) -or -not (Test-Path -LiteralPath $sevenZipSfx)) {
    throw "找不到 7-Zip：$sevenZip"
}
if (-not (Test-Path -LiteralPath (Join-Path $runtimeSource "python\pythonw.exe"))) {
    throw "找不到已准备好的 Python 运行时：$runtimeSource"
}
if (-not $release.StartsWith($project, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "构建目录越界。"
}

Write-Host "[1/6] 清理构建目录..." -ForegroundColor Cyan
if (Test-Path -LiteralPath $release) {
    Remove-Item -LiteralPath $release -Recurse -Force
}
New-Item -ItemType Directory -Path $payload -Force | Out-Null
New-Item -ItemType Directory -Path $appRoot -Force | Out-Null

Write-Host "[2/6] 复制应用源码..." -ForegroundColor Cyan
$excludeDirs = @(".venv", ".uv-cache", ".session", "logs", "dist", "build", "__pycache__", ".git", "packaging")
Get-ChildItem -LiteralPath $project -Force | Where-Object {
    $excludeDirs -notcontains $_.Name
} | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $appRoot -Recurse -Force
}
Get-ChildItem -LiteralPath $payload -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force
foreach ($unwanted in @(".env", "`$null", "DesktopAgent.spec", "logs", ".session")) {
    $path = Join-Path $appRoot $unwanted
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}

Write-Host "[3/6] 复制自带 Python 运行时..." -ForegroundColor Cyan
Copy-Item -LiteralPath $runtimeSource -Destination (Join-Path $payload "runtime") -Recurse -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "启动 Desktop Agent.vbs") -Destination $payload -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "启动 Desktop Agent（调试）.cmd") -Destination $payload -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "Uninstall.cmd") -Destination $payload -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "Uninstaller.vbs") -Destination $payload -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "Shortcuts.vbs") -Destination $payload -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "安装 Desktop Agent.cmd") -Destination $payload -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "源码说明.txt") -Destination $payload -Force
New-Item -ItemType Directory -Path (Join-Path $payload "docs") -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $project "docs\安装与使用说明.md") -Destination (Join-Path $payload "docs") -Force
Copy-Item -LiteralPath (Join-Path $project "docs\安装与使用说明.pdf") -Destination (Join-Path $payload "docs") -Force
Copy-Item -LiteralPath (Join-Path $project "docs\screenshots") -Destination (Join-Path $payload "docs") -Recurse -Force
if (Test-Path -LiteralPath (Join-Path $project "docs\GITHUB_URL.txt")) {
    Copy-Item -LiteralPath (Join-Path $project "docs\GITHUB_URL.txt") -Destination (Join-Path $payload "docs") -Force
}

Write-Host "[4/6] 检查交付文件..." -ForegroundColor Cyan
$required = @(
    "app\main.py",
    "app\agent\loop.py",
    "app\server\main.py",
    "runtime\python\pythonw.exe",
    "docs\安装与使用说明.pdf",
    "docs\安装与使用说明.md",
    "启动 Desktop Agent.vbs",
    "安装 Desktop Agent.cmd",
    "Shortcuts.vbs"
)
foreach ($item in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $payload $item))) {
        throw "缺少交付文件：$item"
    }
}

Write-Host "[5/6] 生成便携版 ZIP..." -ForegroundColor Cyan
$version = "1.2.0"
$zipPath = Join-Path $release "DesktopAgent-$version-Portable.zip"
if (-not $SkipPortableZip) {
    Push-Location $payload
    try {
        & $sevenZip a -tzip -mcu=on -mx=7 $zipPath "*" | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "7-Zip 生成 ZIP 失败（$LASTEXITCODE）。" }
    } finally {
        Pop-Location
    }
}

Write-Host "[6/6] 生成 Windows 安装器..." -ForegroundColor Cyan
New-Item -ItemType Directory -Path $sfxRoot -Force | Out-Null
Copy-Item -LiteralPath $payload -Destination (Join-Path $sfxRoot "payload") -Recurse -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "安装 Desktop Agent.cmd") -Destination (Join-Path $sfxRoot "Install.cmd") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "sfx-config.txt") -Destination (Join-Path $sfxRoot "config.txt") -Force
$webviewBootstrapper = Join-Path $PSScriptRoot "MicrosoftEdgeWebView2Setup.exe"
if (Test-Path -LiteralPath $webviewBootstrapper) {
    Copy-Item -LiteralPath $webviewBootstrapper -Destination $sfxRoot -Force
}

$archivePath = Join-Path $release "installer.7z"
Push-Location $sfxRoot
try {
    $items = @("config.txt", "Install.cmd", "payload")
    if (Test-Path -LiteralPath (Join-Path $sfxRoot "MicrosoftEdgeWebView2Setup.exe")) {
        $items += "MicrosoftEdgeWebView2Setup.exe"
    }
    & $sevenZip a -t7z -mx=7 $archivePath @items | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "7-Zip 生成安装包失败（$LASTEXITCODE）。" }
} finally {
    Pop-Location
}

$setupPath = Join-Path $release "DesktopAgent-$version-Setup.exe"
cmd.exe /c "copy /b /y `"$sevenZipSfx`"+`"$archivePath`" `"$setupPath`"" | Out-Null
if (-not (Test-Path -LiteralPath $setupPath)) { throw "安装器没有生成。" }
Remove-Item -LiteralPath $archivePath -Force

$setupMb = [math]::Round((Get-Item -LiteralPath $setupPath).Length / 1MB, 1)
$zipMb = if (Test-Path -LiteralPath $zipPath) { [math]::Round((Get-Item -LiteralPath $zipPath).Length / 1MB, 1) } else { 0 }
Write-Host ""
Write-Host "构建完成：" -ForegroundColor Green
Write-Host "  安装器：$setupPath ($setupMb MB)"
Write-Host "  便携包：$zipPath ($zipMb MB)"
