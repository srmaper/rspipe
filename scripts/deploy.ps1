param()

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\_py.ps1"
Set-RspipeConsole

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "============================================"
Write-Host "  rspipe 一键部署环境"
Write-Host "  1) 安装 Python 依赖 (pip install -e .[dev])"
Write-Host "  2) 安装并构建前端 (npm install && npm run build)"
Write-Host "============================================"
Write-Host ""

$py = Get-RspipePython
if (-not $py) {
    Write-Host "[错误] 未找到 Python 3.11 或更高版本" -ForegroundColor Red
    $sys = Get-Command python -ErrorAction SilentlyContinue
    if ($sys) {
        Write-Host "当前 python 版本："
        & python -V
        Write-Host ""
        Write-Host "提示：3.12 可以和本机已有的旧版本共存，脚本会自动优先使用 3.12 / 3.11。"
    }
    Write-Host ""
    Write-Host "请任选一种方式安装："
    Write-Host "  1) 在本窗口自动安装（需要 winget）——输入 y 回车"
    Write-Host "  2) 手动下载：https://www.python.org/downloads/windows/"
    Write-Host "     安装时务必勾选 ""Add python.exe to PATH"""
    Write-Host ""
    $ans = Read-Host "现在自动安装 Python 3.12 吗? [y/N]"
    if ($ans -match '^(y|yes)$') {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Write-Host "[错误] 本机没有 winget，请用方式 2 手动安装" -ForegroundColor Red
        } else {
            winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
            Write-Host ""
            Write-Host "安装结束后请关闭本窗口，重新双击「一键部署环境.bat」。"
        }
    }
    Write-Host ""
    Read-Host "按回车键退出"
    exit 1
}

$ver = (& $py -V 2>&1) -join ''
Write-Host "[Python] $ver  ($py)" -ForegroundColor Cyan

Write-Host "[1/2] 安装 Python 依赖..."
& $py -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] Python 依赖安装失败" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}

$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) {
    Write-Host "[跳过] 未检测到 npm（Node.js），保留现有 web\dist"
} else {
    Write-Host ""
    Write-Host "[2/2] 安装并构建前端..."
    Push-Location (Join-Path $root 'web')
    & npm install --registry=https://registry.npmmirror.com
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[警告] npm install 失败，保留现有 dist" -ForegroundColor Yellow
    } else {
        & npm run build
        if ($LASTEXITCODE -ne 0) { Write-Host "[警告] 前端构建失败，保留现有 dist" -ForegroundColor Yellow }
    }
    Pop-Location
}

Write-Host ""
Write-Host "============================================"
Write-Host "  部署完成。双击「一键启动服务.bat」启动平台"
Write-Host "============================================"
Read-Host "按回车键退出"
exit 0
