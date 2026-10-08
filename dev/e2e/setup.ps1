<#
.SYNOPSIS
  前端运行时自动化（评估稿 A1 / dev/README §6.4 前置 3）的隔离依赖下载器。
  把「可编程浏览器驱动」全部下载到本目录（dev/e2e/），不污染机器全局环境。

.DESCRIPTION
  下载两样东西到本文件夹：
    1. pkgs/      —— Playwright 的 Python 包（pip install --target，不进全局 site-packages）
    2. browsers/  —— 浏览器内核（PLAYWRIGHT_BROWSERS_PATH 指向这里）
  跑完后生成 env.ps1（设置 PYTHONPATH 加 PLAYWRIGHT_BROWSERS_PATH），供自动化脚本加载。

  注意：本脚本只下载、不跑测试；也不造夹具库（那是数据，见 §6.4 前置 2，下载不到）。
  零成本替代：本机已装 Edge，可走 CDP 直连（--remote-debugging-port），无需下载。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File dev\e2e\setup.ps1

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File dev\e2e\setup.ps1 `
    -PipIndex "https://pypi.tuna.tsinghua.edu.cn/simple" `
    -BrowserHost "https://npmmirror.com/mirrors/playwright/"

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File dev\e2e\setup.ps1 -NoBrowser
#>
param(
  [string]$Python = "D:\Program Files (x86)\Pythons\cpython-3.13.15-windows-x86_64-none\python.exe",
  [ValidateSet("chromium", "firefox", "webkit")][string]$Browser = "chromium",
  [string]$PipIndex = "",
  [string]$BrowserHost = "",
  [switch]$NoBrowser,
  [switch]$Force
)

$ErrorActionPreference = "Stop"

$Root     = $PSScriptRoot
$Pkgs     = Join-Path $Root "pkgs"
$Browsers = Join-Path $Root "browsers"
$EnvFile  = Join-Path $Root "env.ps1"

function Say($m)  { Write-Host "  $m" }
function Head($m) { Write-Host ("`n" + "=" * 4 + " " + $m) -ForegroundColor Cyan }
function Warn($m) { Write-Host ("  [!] " + $m) -ForegroundColor Yellow }

Write-Host ("=" * 68)
Write-Host "  前端自动化依赖下载器 · 隔离到 dev/e2e/"
Write-Host ("  目标目录：" + $Root)
Write-Host ("=" * 68)

# ---- 0. 检查解释器 ----
Head "检查解释器"
if (-not (Test-Path -LiteralPath $Python)) {
  $alt = Get-Command python -ErrorAction SilentlyContinue
  if ($alt) { $Python = $alt.Source; Warn ("指定解释器不存在，回退到 PATH：" + $Python) }
  else { throw ("找不到 Python 解释器：" + $Python + "（用 -Python <路径> 指定）") }
}
$ver = (& $Python --version 2>&1) -join " "
Say ("解释器：" + $Python)
Say ("版本  ：" + $ver)

# ---- 1. 目录 ----
Head "准备目录"
if ($Force) {
  foreach ($d in @($Pkgs, $Browsers)) {
    if (Test-Path -LiteralPath $d) { Remove-Item -LiteralPath $d -Recurse -Force }
  }
  Say "-Force：已清空旧目录"
}
foreach ($d in @($Pkgs, $Browsers)) {
  if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}
Say ("pkgs     -> " + $Pkgs)
Say ("browsers -> " + $Browsers)

# ---- 2. pip 安装 Playwright 到隔离目录 ----
Head "pip 安装 playwright（--target 隔离）"
$pipArgs = @("-m", "pip", "install", "--target", $Pkgs, "--upgrade", "playwright")
if ($PipIndex) { $pipArgs += @("-i", $PipIndex) }
Say ("执行：& `"" + $Python + "`" " + ($pipArgs -join " "))
& $Python @pipArgs
if ($LASTEXITCODE -ne 0) { throw ("pip 安装失败（退出码 " + $LASTEXITCODE + "）") }
Say "OK：playwright 已装入 pkgs/"

# ---- 3. 下载浏览器内核到隔离目录 ----
if ($NoBrowser) {
  Head "跳过浏览器内核（-NoBrowser）"
  Say "仅装了包；驱动本机 Edge 时用 CDP，无需内核。"
} else {
  Head ("下载浏览器内核：" + $Browser + "（PLAYWRIGHT_BROWSERS_PATH 指向 browsers/）")
  if ($BrowserHost) { $env:PLAYWRIGHT_DOWNLOAD_HOST = $BrowserHost; Say ("内核镜像：" + $BrowserHost) }
  $env:PLAYWRIGHT_BROWSERS_PATH = $Browsers
  $env:PYTHONPATH = $Pkgs
  Say ("执行：& `"" + $Python + "`" -m playwright install " + $Browser)
  & $Python -m playwright install $Browser
  if ($LASTEXITCODE -ne 0) { throw ("浏览器内核下载失败（退出码 " + $LASTEXITCODE + "）") }
  Say "OK：内核已装入 browsers/"
}

# ---- 4. 生成 env.ps1 ----
Head "生成 env.ps1（供自动化脚本加载）"
$envContent = @"
# 由 dev/e2e/setup.ps1 生成 —— 每台机器路径不同，故不入库（见 .gitignore）。
`$env:PYTHONPATH = "$Pkgs"
`$env:PLAYWRIGHT_BROWSERS_PATH = "$Browsers"
# 可选：绕过本机代理（§四 约定）
`$env:NO_PROXY = "127.0.0.1,localhost"
`$env:no_proxy = "127.0.0.1,localhost"
"@
Set-Content -LiteralPath $EnvFile -Value $envContent -Encoding UTF8
Say ("已写：" + $EnvFile)

# ---- 5. 自检 ----
Head "自检（导入 playwright）"
$env:PYTHONPATH = $Pkgs
$env:PLAYWRIGHT_BROWSERS_PATH = $Browsers
& $Python -c "import playwright; print('playwright ok')" 2>&1 | ForEach-Object { Say $_ }
if (-not $NoBrowser) {
  $cnt = (Get-ChildItem -LiteralPath $Browsers -Directory -ErrorAction SilentlyContinue | Measure-Object).Count
  Say ("browsers/ 下内核目录数：" + $cnt)
}

Write-Host ("`n" + "=" * 68)
Write-Host "  OK 完成。用法：先加载环境，再跑自动化脚本"
Write-Host "      . dev\e2e\env.ps1"
Write-Host ("      & `"" + $Python + "`" dev\e2e\<你的前端冒烟脚本>.py")
Write-Host ("=" * 68)